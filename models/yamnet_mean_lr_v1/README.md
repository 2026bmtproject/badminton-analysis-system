# Frozen cheer detector

`model.npz` and `metadata.json` are exact copies of the production export from
`badminton-audio-highlight` at commit `f72eb4c36d2f47a2c187122ebdfe26cfbb63eed2`.
Model SHA-256:
`c5257098315fe51db163039cca95917dd482d1612e1f08b95d301a0dbf8f79f8`.

The stage resolves this directory relative to its source file and checks the
frozen SHA before loading NumPy arrays with `allow_pickle=False`. Metadata
records training provenance; those dataset paths are descriptive and are never
opened during inference. YAMNet itself is loaded separately from
`https://tfhub.dev/google/yamnet/1` and uses the TFHub cache.

Do not regenerate, refit, or change these parameters for the v1 producer.
