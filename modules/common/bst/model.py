"""The BST network (BST_CG_AP) and its checkpoint loader.

Badminton Stroke-type Transformer, Clean-Gate / Aim-Player variant. Given a short
window of both players' skeletons and the shuttle's trajectory, it answers "which
stroke, by whom" as a single 25-way choice (see :mod:`modules.common.bst.classes`).

How it reads a rally window
---------------------------
Three streams — player 1's joints+bones, player 2's, and the shuttle — are each run
through a TCN and a temporal transformer, giving one summary token per stream. Two
mechanisms then decide *who hit it*, which is the hard half of the problem:

* **Aim-Player.** Each player is cross-attended against the shuttle, and the resulting
  two tokens are compared to the shuttle's own token by cosine similarity. Whoever's
  motion the shuttle "agrees" with more gets weight ``alpha``; the other gets
  ``1 - alpha``. So the hitter is chosen by shuttle-player coupling rather than by any
  hand-written rule about who is nearer.
* **Clean-Gate.** Whatever is present in *both* players' shuttle-attended tokens cannot
  be what distinguishes them — it is the rally's shared context. That elementwise
  minimum is passed through an MLP and subtracted from the shuttle token.

The court position of each player is not concatenated as an extra feature but folded in
multiplicatively (``JnB * mlp(pos) + JnB``): the same arm motion means a different
stroke at the net than at the baseline, so position modulates the pose stream instead of
sitting beside it.

This is a faithful port of the reference implementation — the architecture must stay
bit-compatible with the published checkpoint, so nothing here is "cleaned up".
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch import Tensor, nn

from modules.common.bst.classes import IN_DIM, N_CLASSES, SEQ_LEN
from modules.common.config import repo_root

#: Trained weights. Not in the repo — download it (see README) and drop it in ``models/``.
DEFAULT_WEIGHT = "models/bst_CG_JnB_bone_between_2_hits_with_max_limits_seq_100_merged.pt"


def resolve_weight(path: str | Path | None = None) -> Path:
    """Absolute path to the BST checkpoint; relative paths resolve from the repo root."""
    p = Path(path or DEFAULT_WEIGHT)
    return p if p.is_absolute() else repo_root() / p


def default_device() -> str:
    """``"cuda"`` when torch can see a GPU, else ``"cpu"``.

    Unlike ``pose``, a silent CPU fallback here is not a disaster worth shouting about:
    BST runs on ~100-frame windows of already-extracted keypoints, so it is cheap next to
    the two stages that feed it. It is still worth having the GPU when there is one, since
    ``event_detection`` runs one window *per frame*.
    """
    return "cuda" if torch.cuda.is_available() else "cpu"


class PositionalEncoding1D(nn.Module):
    """Standard sinusoidal encoding, inlined.

    Only ever used to *initialize* ``embedding_*`` below, which are ``nn.Parameter`` and
    therefore overwritten wholesale by ``load_state_dict``. It exists so the module can be
    constructed at all, and has no effect on the output of a loaded model.
    """

    def __init__(self, channels: int) -> None:
        super().__init__()
        self.org_channels = channels
        channels = int(np.ceil(channels / 2) * 2)
        self.channels = channels
        inv_freq = 1.0 / (10000 ** (torch.arange(0, channels, 2).float() / channels))
        self.register_buffer("inv_freq", inv_freq)

    def forward(self, tensor: Tensor) -> Tensor:
        if tensor.dim() != 3:
            raise RuntimeError("The input tensor has to be 3d!")
        _, x, orig_ch = tensor.shape
        pos_x = torch.arange(x, device=tensor.device).type(self.inv_freq.type())
        sin_inp_x = torch.einsum("i,j->ij", pos_x, self.inv_freq)
        emb_x = torch.cat((sin_inp_x.sin(), sin_inp_x.cos()), dim=-1)
        emb = torch.zeros((x, self.channels), device=tensor.device).type(tensor.type())
        emb[:, : self.channels] = emb_x
        return emb[None, :, :orig_ch].repeat(tensor.shape[0], 1, 1)


class MLP(nn.Module):
    def __init__(self, in_dim, out_dim, hd_dim, drop_p=0.0) -> None:
        super().__init__()
        self.mlp = nn.Sequential(
            nn.Linear(in_dim, hd_dim), nn.GELU(),
            nn.Dropout(drop_p, inplace=True), nn.Linear(hd_dim, out_dim),
        )

    def forward(self, x: Tensor):
        return self.mlp(x)


class MLP_Head(nn.Module):
    def __init__(self, in_dim, out_dim, hd_dim, drop_p=0.0) -> None:
        super().__init__()
        self.layer_norm = nn.LayerNorm(in_dim)
        self.mlp = MLP(in_dim, out_dim, hd_dim, drop_p)

    def forward(self, x: Tensor):
        return self.mlp(self.layer_norm(x))


class FeedForward(nn.Module):
    def __init__(self, in_dim, out_dim, hd_dim, drop_p=0.0) -> None:
        super().__init__()
        self.mlp = MLP(in_dim, out_dim, hd_dim, drop_p)
        self.dropout = nn.Dropout(drop_p, inplace=True)

    def forward(self, x: Tensor):
        return self.dropout(self.mlp(x))


class MultiHeadAttention(nn.Module):
    def __init__(self, d_model, d_head, n_head, drop_p) -> None:
        super().__init__()
        d_cat = d_head * n_head
        self.h = n_head
        self.to_qkv = nn.Linear(d_model, d_cat * 3, bias=False)
        self.scale = d_head ** -0.5
        self.attend = nn.Sequential(nn.Softmax(dim=-1), nn.Dropout(drop_p))
        self.tail = nn.Sequential(
            nn.Linear(d_cat, d_model), nn.Dropout(drop_p, inplace=True)
        ) if n_head != 1 or d_cat != d_model else nn.Identity()

    def forward(self, x: Tensor, mask: Tensor = None):
        bn, t, _ = x.shape
        qkv = self.to_qkv(x).view(bn, t, self.h, -1).chunk(3, dim=-1)
        q, k, v = map(lambda ts: ts.transpose(1, 2), qkv)
        dots = (q.contiguous() @ k.transpose(-1, -2).contiguous()) * self.scale
        if mask is not None:
            mask = mask.view(bn, 1, 1, t)
            dots = dots.masked_fill(mask == 0.0, -torch.inf)
        coef = self.attend(dots)
        out = (coef @ v.contiguous()).transpose(1, 2).reshape(bn, t, -1)
        return self.tail(out)


class TransformerLayer(nn.Module):
    def __init__(self, d_model, d_head, n_head, hd_mlp, drop_p) -> None:
        super().__init__()
        self.layer_norm1 = nn.LayerNorm(d_model)
        self.attn = MultiHeadAttention(d_model, d_head, n_head, drop_p)
        self.layer_norm2 = nn.LayerNorm(d_model)
        self.ff = FeedForward(d_model, d_model, hd_mlp, drop_p)

    def forward(self, x: Tensor, mask=None):
        x = self.attn(self.layer_norm1(x), mask) + x
        x = self.ff(self.layer_norm2(x)) + x
        return x


class TransformerEncoder(nn.Module):
    def __init__(self, d_model, d_head, n_head, depth, hd_mlp, drop_p) -> None:
        super().__init__()
        self.layers = nn.ModuleList(
            [TransformerLayer(d_model, d_head, n_head, hd_mlp, drop_p) for _ in range(depth)]
        )

    def forward(self, x: Tensor, mask=None):
        for layer in self.layers:
            x = layer(x, mask)
        return x


class TCN(nn.Module):
    def __init__(self, in_channel, channels, kernel_size=5, drop_p=0.3) -> None:
        super().__init__()
        layers = []
        for i in range(len(channels)):
            in_ch = in_channel if i == 0 else channels[i - 1]
            out_ch = channels[i]
            dilation = i * 2 + 1
            padding = (kernel_size - 1) * dilation // 2
            layers += [
                nn.Conv1d(in_ch, out_ch, kernel_size, padding=padding, dilation=dilation),
                nn.BatchNorm1d(out_ch), nn.GELU(), nn.Dropout(drop_p, inplace=True),
            ]
        self.net = nn.Sequential(*layers)

    def forward(self, x: Tensor):
        return self.net(x)


class MultiHeadCrossAttention(nn.Module):
    def __init__(self, d_model, d_head, n_head, drop_p) -> None:
        super().__init__()
        d_cat = d_head * n_head
        self.h = n_head
        self.to_q = nn.Linear(d_model, d_cat, bias=False)
        self.to_kv = nn.Linear(d_model, d_cat * 2, bias=False)
        self.scale = d_head ** -0.5
        self.attend = nn.Sequential(nn.Softmax(dim=-1), nn.Dropout(drop_p))
        self.tail = nn.Sequential(
            nn.Linear(d_cat, d_model), nn.Dropout(drop_p, inplace=True)
        ) if n_head != 1 or d_cat != d_model else nn.Identity()

    def forward(self, x1: Tensor, x2: Tensor, mask: Tensor = None):
        q = self.to_q(x1)
        kv = self.to_kv(x2)
        b, t, _ = q.shape
        q = q.view(b, t, self.h, -1).transpose(1, 2)
        kv = kv.view(b, t, self.h, -1).chunk(2, dim=-1)
        k, v = map(lambda ts: ts.transpose(1, 2), kv)
        dots = (q.contiguous() @ k.transpose(-1, -2).contiguous()) * self.scale
        if mask is not None:
            mask = mask.view(b, 1, 1, t)
            dots = dots.masked_fill(mask == 0.0, -torch.inf)
        coef = self.attend(dots)
        out = (coef @ v.contiguous()).transpose(1, 2).reshape(b, t, -1)
        return self.tail(out)


class CrossTransformerLayer(nn.Module):
    def __init__(self, d_model, d_head, n_head, hd_mlp, drop_p) -> None:
        super().__init__()
        self.layer_norm1_x1 = nn.LayerNorm(d_model)
        self.layer_norm1_x2 = nn.LayerNorm(d_model)
        self.cross_attn = MultiHeadCrossAttention(d_model, d_head, n_head, drop_p)
        self.layer_norm2 = nn.LayerNorm(d_model)
        self.ff = FeedForward(d_model, d_model, hd_mlp, drop_p)

    def forward(self, x1: Tensor, x2: Tensor, mask=None):
        x1 = self.layer_norm1_x1(x1)
        x2 = self.layer_norm1_x2(x2)
        x = self.cross_attn(x1, x2, mask)
        z = self.layer_norm2(x)
        x = self.ff(z) + x
        return x


class BST_CG_AP(nn.Module):
    """BST_CleanGate_AimPlayer — PPF + Clean Gate + Cosine Similarity."""

    def __init__(
        self, in_dim, seq_len, n_class=35, n_people=2,
        d_model=100, d_head=128, n_head=6, depth_tem=2, depth_inter=1,
        drop_p=0.3, mlp_d_scale=4, tcn_kernel_size=5,
    ):
        super().__init__()
        if n_people > 2:
            raise NotImplementedError

        self.mlp_positions = MLP(2, out_dim=in_dim, hd_dim=256, drop_p=drop_p)
        self.tcn_pose = TCN(in_dim, [d_model, d_model], tcn_kernel_size, drop_p)
        self.tcn_shuttle = TCN(2, [d_model // 2, d_model], tcn_kernel_size, drop_p)

        self.learned_token_tem = nn.Parameter(torch.randn(1, d_model))
        self.embedding_tem = nn.Parameter(torch.empty(1, 1 + seq_len, d_model))
        self.pre_dropout = nn.Dropout(drop_p, inplace=True)
        self.encoder_tem = TransformerEncoder(
            d_model, d_head, n_head, depth_tem, d_model * mlp_d_scale, drop_p)

        self.embedding_cross = nn.Parameter(torch.empty(1, seq_len, d_model))
        self.cross_trans = CrossTransformerLayer(
            d_model, d_head, n_head, d_model * mlp_d_scale, drop_p)

        self.learned_token_inter = nn.Parameter(torch.randn(1, d_model))
        self.embedding_inter = nn.Parameter(torch.empty(1, 1 + seq_len, d_model))
        self.encoder_inter = TransformerEncoder(
            d_model, d_head, n_head, depth_inter, d_model * mlp_d_scale, drop_p)

        self.cos_sim = nn.CosineSimilarity()
        self.mlp_clean = MLP(d_model, d_model, d_model, drop_p)
        self.mlp_head = MLP_Head(d_model * 3, n_class, d_model * mlp_d_scale, drop_p)

        self.d_model = d_model
        self.init_weights()

    @torch.no_grad()
    def init_weights(self):
        p_enc = PositionalEncoding1D(self.d_model)
        self.embedding_tem.copy_(p_enc(self.embedding_tem))
        self.embedding_cross.copy_(p_enc(self.embedding_cross))
        self.embedding_inter.copy_(p_enc(self.embedding_inter))
        nn.init.normal_(self.learned_token_tem, std=0.02)
        nn.init.normal_(self.learned_token_inter, std=0.02)
        self.apply(self.init_weights_recursive)

    def init_weights_recursive(self, m):
        if isinstance(m, nn.Linear):
            nn.init.xavier_uniform_(m.weight)
            if m.bias is not None:
                nn.init.constant_(m.bias, 0)
        elif isinstance(m, nn.Conv1d):
            nn.init.xavier_normal_(m.weight)

    def forward(self, JnB: Tensor, shuttle: Tensor, pos: Tensor, video_len: Tensor):
        """``JnB`` (b, t, 2, in_dim), ``shuttle`` (b, t, 2), ``pos`` (b, t, 2, 2),
        ``video_len`` (b,) — how many of the ``t`` frames are real rather than padding.
        Returns the (b, n_class) logits."""
        b, t, n, in_dim = JnB.shape
        JnB = JnB.permute(0, 2, 3, 1).reshape(b * n, in_dim, t)

        pos = self.mlp_positions(pos)
        pos_impact = pos.permute(0, 2, 3, 1).reshape(b * n, in_dim, t)
        JnB = JnB * pos_impact + JnB

        JnB = self.tcn_pose(JnB)
        JnB = JnB.view(b, n, -1, t).transpose(-2, -1)

        shuttle = shuttle.transpose(1, 2).contiguous()
        shuttle = self.tcn_shuttle(shuttle)
        shuttle = shuttle.unsqueeze(1).transpose(-2, -1)

        x = torch.cat((JnB, shuttle), dim=1)
        _, n, _, d = x.shape

        class_token_tem = self.learned_token_tem.view(1, 1, -1).expand(b * n, -1, -1)
        x = x.view(b * n, t, d)
        x = torch.cat((class_token_tem, x), dim=1) + self.embedding_tem

        range_t = torch.arange(0, 1 + t, device=x.device).unsqueeze(0).expand(b, -1)
        video_len = video_len.unsqueeze(-1)
        mask = range_t < (1 + video_len)
        mask_n = mask.repeat_interleave(n, dim=0)

        x = self.pre_dropout(x)
        x = self.encoder_tem(x, mask_n)
        x = x.view(b, n, 1 + t, d)

        p1, p2, shuttle = map(lambda ts: ts.squeeze(1), x.chunk(3, dim=1))
        p1_cls, p2_cls, shuttle_cls = \
            p1[:, 0].contiguous(), p2[:, 0].contiguous(), shuttle[:, 0].contiguous()

        p1 = p1[:, 1:].contiguous() + self.embedding_cross
        p2 = p2[:, 1:].contiguous() + self.embedding_cross
        shuttle = shuttle[:, 1:].contiguous() + self.embedding_cross

        cross_mask = mask[:, 1:].contiguous()
        p1_shuttle = self.cross_trans(p1, shuttle, cross_mask)
        p2_shuttle = self.cross_trans(p2, shuttle, cross_mask)

        class_token_inter = self.learned_token_inter.view(1, 1, -1).expand(b, -1, -1)
        p1_shuttle = torch.cat((class_token_inter, p1_shuttle), dim=1) + self.embedding_inter
        p2_shuttle = torch.cat((class_token_inter, p2_shuttle), dim=1) + self.embedding_inter

        p1_shuttle = self.encoder_inter(p1_shuttle, mask)
        p2_shuttle = self.encoder_inter(p2_shuttle, mask)

        p1_shuttle_cls = p1_shuttle[:, 0, :].contiguous()
        p2_shuttle_cls = p2_shuttle[:, 0, :].contiguous()

        # Aim-Player: the shuttle picks the hitter, by agreeing more with their motion.
        p1_shuttle_sim = self.cos_sim(p1_shuttle_cls, shuttle_cls)
        p2_shuttle_sim = self.cos_sim(p2_shuttle_cls, shuttle_cls)
        alpha = ((p1_shuttle_sim - p2_shuttle_sim + 2) / 4).unsqueeze(1)

        p1_conclusion = alpha * (p1_cls + p1_shuttle_cls)
        p2_conclusion = (1 - alpha) * (p2_cls + p2_shuttle_cls)

        # Clean-Gate: what both players share cannot tell them apart, so subtract it.
        info_need_clean = torch.minimum(p1_shuttle_cls, p2_shuttle_cls)
        dirt = self.mlp_clean(info_need_clean)
        shuttle_cls = shuttle_cls - dirt

        x = torch.cat((p1_conclusion, p2_conclusion, shuttle_cls), dim=1)
        return self.mlp_head(x)


def build_bst_model() -> BST_CG_AP:
    """An untrained BST with the exact geometry the published checkpoint was saved from."""
    return BST_CG_AP(
        in_dim=IN_DIM, n_class=N_CLASSES, seq_len=SEQ_LEN,
        depth_tem=2, depth_inter=1,
    )


def load_bst_model(weight_path: str | Path | None = None, device: str = "cpu") -> BST_CG_AP:
    """Load the trained BST onto ``device``, in eval mode and ready for inference."""
    weight = resolve_weight(weight_path)
    if not weight.is_file():
        raise FileNotFoundError(
            f"BST weight not found: {weight}. Download it (see README) and place it "
            "under models/."
        )
    model = build_bst_model()
    model.load_state_dict(torch.load(weight, map_location=device, weights_only=True))
    model.to(device)
    model.eval()
    return model
