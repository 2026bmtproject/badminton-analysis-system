const os = require("node:os");

try {
  os.userInfo();
} catch (error) {
  if (error?.syscall !== "uv_os_get_passwd") throw error;
  os.userInfo = () => ({
    uid: -1,
    gid: -1,
    username: process.env.USERNAME || "local-user",
    homedir: process.env.USERPROFILE || os.homedir(),
    shell: null,
  });
}

// Child Node processes (including tests) need the same guarded workaround.
const option = `--require=${JSON.stringify(__filename)}`;
if (!(process.env.NODE_OPTIONS || "").includes(__filename)) {
  process.env.NODE_OPTIONS = [option, process.env.NODE_OPTIONS]
    .filter(Boolean)
    .join(" ");
}
