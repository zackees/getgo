#include <cosmo.h>
#include <libc/nt/runtime.h>
#include <limits.h>
#include <signal.h>
#include <spawn.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cerrno>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

extern char **environ;

#ifndef GETGO_VERSION
#define GETGO_VERSION "0.1.0"
#endif

namespace {

constexpr const char *kUsage = "Usage: getgo <package> [<package>...]";
constexpr const char *kUnixInstaller = "https://astral.sh/uv/install.sh";
constexpr const char *kWindowsInstaller = "https://astral.sh/uv/install.ps1";
[[gnu::used]] constexpr const char kBuildMetadata[] =
    "getgo build toolchain: clang-tool-chain 1.5.8; cosmocc fat x86_64+aarch64";

bool IsWindowsHost() { return IsWindows(); }

int DecodeStatus(int status) {
  if (WIFEXITED(status)) return WEXITSTATUS(status);
  if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
  return 1;
}

bool WaitFor(pid_t child, int *status) {
  for (;;) {
    if (waitpid(child, status, 0) >= 0) return true;
    if (errno == EINTR) continue;
    std::fprintf(stderr, "getgo: wait failed: %s\n", std::strerror(errno));
    return false;
  }
}

std::vector<char *> MutableArgv(const std::vector<std::string> &arguments) {
  std::vector<char *> result;
  result.reserve(arguments.size() + 1);
  for (const std::string &argument : arguments) {
    result.push_back(const_cast<char *>(argument.c_str()));
  }
  result.push_back(nullptr);
  return result;
}

int Spawn(const std::vector<std::string> &arguments, int stdout_fd = -1, std::string *output = nullptr) {
  if (arguments.empty()) return 1;
  int pipe_fds[2] = {-1, -1};
  if (output && pipe(pipe_fds) == -1) {
    std::fprintf(stderr, "getgo: pipe failed: %s\n", std::strerror(errno));
    return 1;
  }

  posix_spawn_file_actions_t actions;
  posix_spawn_file_actions_init(&actions);
  if (stdout_fd >= 0) {
    posix_spawn_file_actions_adddup2(&actions, stdout_fd, STDOUT_FILENO);
  } else if (output) {
    posix_spawn_file_actions_adddup2(&actions, pipe_fds[1], STDOUT_FILENO);
    posix_spawn_file_actions_addclose(&actions, pipe_fds[0]);
    posix_spawn_file_actions_addclose(&actions, pipe_fds[1]);
  }

  std::vector<char *> argv = MutableArgv(arguments);
  pid_t child = 0;
  int spawn_error = posix_spawn(&child, arguments[0].c_str(), &actions, nullptr, argv.data(), environ);
  posix_spawn_file_actions_destroy(&actions);
  if (output) close(pipe_fds[1]);
  if (spawn_error) {
    if (output) close(pipe_fds[0]);
    std::fprintf(stderr, "getgo: failed to run %s: %s\n", arguments[0].c_str(), std::strerror(spawn_error));
    return 1;
  }

  if (output) {
    char buffer[4096];
    ssize_t count;
    while ((count = read(pipe_fds[0], buffer, sizeof(buffer))) > 0) {
      output->append(buffer, static_cast<size_t>(count));
    }
    close(pipe_fds[0]);
  }

  int status = 0;
  if (!WaitFor(child, &status)) return 1;
  return DecodeStatus(status);
}

int SpawnPipeline(const std::vector<std::string> &producer, const std::vector<std::string> &consumer) {
  int pipe_fds[2];
  if (pipe(pipe_fds) == -1) {
    std::fprintf(stderr, "getgo: pipe failed: %s\n", std::strerror(errno));
    return 1;
  }

  posix_spawn_file_actions_t producer_actions;
  posix_spawn_file_actions_init(&producer_actions);
  posix_spawn_file_actions_adddup2(&producer_actions, pipe_fds[1], STDOUT_FILENO);
  posix_spawn_file_actions_addclose(&producer_actions, pipe_fds[0]);
  posix_spawn_file_actions_addclose(&producer_actions, pipe_fds[1]);

  std::vector<char *> producer_argv = MutableArgv(producer);
  pid_t producer_child = 0;
  int error = posix_spawn(&producer_child, producer[0].c_str(), &producer_actions, nullptr, producer_argv.data(), environ);
  posix_spawn_file_actions_destroy(&producer_actions);
  if (error) {
    close(pipe_fds[0]);
    close(pipe_fds[1]);
    std::fprintf(stderr, "getgo: failed to run %s: %s\n", producer[0].c_str(), std::strerror(error));
    return 1;
  }

  posix_spawn_file_actions_t consumer_actions;
  posix_spawn_file_actions_init(&consumer_actions);
  posix_spawn_file_actions_adddup2(&consumer_actions, pipe_fds[0], STDIN_FILENO);
  posix_spawn_file_actions_addclose(&consumer_actions, pipe_fds[0]);
  posix_spawn_file_actions_addclose(&consumer_actions, pipe_fds[1]);

  std::vector<char *> consumer_argv = MutableArgv(consumer);
  pid_t consumer_child = 0;
  error = posix_spawn(&consumer_child, consumer[0].c_str(), &consumer_actions, nullptr, consumer_argv.data(), environ);
  posix_spawn_file_actions_destroy(&consumer_actions);
  close(pipe_fds[0]);
  close(pipe_fds[1]);
  if (error) {
    kill(producer_child, SIGTERM);
    int ignored = 0;
    (void)WaitFor(producer_child, &ignored);
    std::fprintf(stderr, "getgo: failed to run %s: %s\n", consumer[0].c_str(), std::strerror(error));
    return 1;
  }

  int producer_status = 0;
  int consumer_status = 0;
  bool producer_waited = WaitFor(producer_child, &producer_status);
  bool consumer_waited = WaitFor(consumer_child, &consumer_status);
  if (!producer_waited || !consumer_waited) return 1;
  int producer_code = DecodeStatus(producer_status);
  return producer_code ? producer_code : DecodeStatus(consumer_status);
}

bool IsFile(const std::string &path) {
  struct stat information;
  return !path.empty() && stat(path.c_str(), &information) == 0 && S_ISREG(information.st_mode) &&
         access(path.c_str(), IsWindowsHost() ? F_OK : X_OK) == 0;
}

std::string AbsolutePath(const std::string &path) {
  if (!path.empty() && (path[0] == '/' || path[0] == '\\')) return path;
  if (path.size() >= 2 && std::isalpha(static_cast<unsigned char>(path[0])) && path[1] == ':') return path;
  char working_directory[PATH_MAX];
  if (!getcwd(working_directory, sizeof(working_directory))) return path;
  std::string result(working_directory);
  if (!result.empty() && result.back() != '/' && result.back() != '\\') result += '/';
  return result + path;
}

std::string Join(const std::string &left, const std::string &right) {
  if (left.empty()) return right;
  char last = left.back();
  if (last == '/' || last == '\\') return left + right;
  // Cosmopolitan presents Windows paths through its POSIX virtual filesystem
  // (for example, C:\\Users becomes /C/Users), so forward slashes are the one
  // separator that works in the same APE on every host.
  return left + "/" + right;
}

std::vector<std::string> ExecutableNames(const std::string &name) {
  if (!IsWindowsHost()) return {name};
  return {name + ".exe", name + ".com", name + ".cmd", name + ".bat", name};
}

std::string FindOnPath(const std::string &name) {
  const char *path_value = std::getenv("PATH");
  if (!path_value) return {};
  const char separator = IsWindowsHost() ? ';' : ':';
  std::string path(path_value);
  size_t start = 0;
  while (start <= path.size()) {
    size_t end = path.find(separator, start);
    if (end == std::string::npos) end = path.size();
    std::string directory = path.substr(start, end - start);
    if (directory.size() >= 2 && directory.front() == '"' && directory.back() == '"') {
      directory = directory.substr(1, directory.size() - 2);
    }
    if (!directory.empty()) {
      for (const std::string &executable : ExecutableNames(name)) {
        std::string candidate = Join(directory, executable);
        if (IsFile(candidate)) return AbsolutePath(candidate);
      }
    }
    if (end == path.size()) break;
    start = end + 1;
  }
  return {};
}

std::string FindUv() {
  std::string found = FindOnPath("uv");
  if (!found.empty()) return found;
  const char *home = std::getenv(IsWindowsHost() ? "USERPROFILE" : "HOME");
  if (!home || !*home) return {};
  std::string binary_directory = Join(Join(home, ".local"), "bin");
  for (const std::string &executable : ExecutableNames("uv")) {
    std::string candidate = Join(binary_directory, executable);
    if (IsFile(candidate)) return AbsolutePath(candidate);
  }
  return {};
}

int BootstrapUv() {
  if (IsWindowsHost()) {
    std::string powershell = FindOnPath("powershell");
    if (powershell.empty()) {
      std::fputs("getgo: PowerShell is required to install uv\n", stderr);
      return 1;
    }
    return Spawn({powershell, "-ExecutionPolicy", "ByPass", "-c", std::string("irm ") + kWindowsInstaller + " | iex"});
  }

  std::string curl = FindOnPath("curl");
  if (!curl.empty()) return SpawnPipeline({curl, "-LsSf", kUnixInstaller}, {"/bin/sh"});
  std::string wget = FindOnPath("wget");
  if (!wget.empty()) return SpawnPipeline({wget, "-qO-", kUnixInstaller}, {"/bin/sh"});
  std::fputs("getgo: curl or wget is required to install uv\n", stderr);
  return 1;
}

bool IsPackageName(const char *value) {
  if (!value || !*value || value[0] == '-') return false;
  size_t length = std::strlen(value);
  if (!std::isalnum(static_cast<unsigned char>(value[0])) ||
      !std::isalnum(static_cast<unsigned char>(value[length - 1]))) {
    return false;
  }
  for (const unsigned char c : std::string(value)) {
    if (!std::isalnum(c) && c != '.' && c != '_' && c != '-') return false;
  }
  return true;
}

int UsageError(const std::string &message) {
  std::fprintf(stderr, "getgo: %s\n%s\n", message.c_str(), kUsage);
  return 2;
}

std::string Trim(std::string value) {
  while (!value.empty() && (value.back() == '\n' || value.back() == '\r' || value.back() == ' ' || value.back() == '\t')) {
    value.pop_back();
  }
  size_t start = 0;
  while (start < value.size() && (value[start] == ' ' || value[start] == '\t')) ++start;
  return value.substr(start);
}

std::string NormalizePath(std::string value) {
  while (value.size() > 1 && (value.back() == '/' || value.back() == '\\')) value.pop_back();
  if (IsWindowsHost()) {
    for (char &c : value) {
      if (c == '/') c = '\\';
      c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
  }
  return value;
}

bool PathContains(const std::string &directory) {
  const char *path_value = std::getenv("PATH");
  if (!path_value) return false;
  const char separator = IsWindowsHost() ? ';' : ':';
  std::string expected = NormalizePath(directory);
  std::string path(path_value);
  size_t start = 0;
  while (start <= path.size()) {
    size_t end = path.find(separator, start);
    if (end == std::string::npos) end = path.size();
    std::string item = path.substr(start, end - start);
    if (item.size() >= 2 && item.front() == '"' && item.back() == '"') item = item.substr(1, item.size() - 2);
    if (!item.empty() && NormalizePath(item) == expected) return true;
    if (end == path.size()) break;
    start = end + 1;
  }
  return false;
}

void FinishPathSetup(const std::string &uv) {
  std::string output;
  int directory_code = Spawn({uv, "tool", "dir", "--bin"}, -1, &output);
  std::string tool_bin = directory_code == 0 ? Trim(output) : std::string();
  (void)Spawn({uv, "tool", "update-shell"});
  if (!tool_bin.empty() && !PathContains(tool_bin)) {
    if (IsWindowsHost()) {
      std::printf("$env:Path = \"%s;$env:Path\"\n", tool_bin.c_str());
    } else {
      std::printf("export PATH=\"%s:$PATH\"\n", tool_bin.c_str());
    }
  }
}

}  // namespace

int ProgramMain(int argc, char **argv) {
  if (argc == 2 && std::strcmp(argv[1], "--help") == 0) {
    std::printf("%s\nInstall one or more PyPI tools with uv.\n", kUsage);
    return 0;
  }
  if (argc == 2 && std::strcmp(argv[1], "--version") == 0) {
    std::printf("getgo %s\n", GETGO_VERSION);
    return 0;
  }
  if (argc < 2) return UsageError("at least one package is required");
  for (int i = 1; i < argc; ++i) {
    if (argv[i][0] == '-') return UsageError(std::string("unsupported option: ") + argv[i]);
    if (!IsPackageName(argv[i])) return UsageError(std::string("invalid PyPI distribution name: ") + argv[i]);
  }

  std::string uv = FindUv();
  if (uv.empty()) {
    int bootstrap_code = BootstrapUv();
    if (bootstrap_code) return bootstrap_code;
    uv = FindUv();
    if (uv.empty()) {
      std::fputs("getgo: the uv installer completed but uv could not be found\n", stderr);
      return 1;
    }
  }

  for (int i = 1; i < argc; ++i) {
    int code = Spawn({uv, "tool", "install", argv[i]});
    if (code) return code;
  }
  FinishPathSetup(uv);
  return 0;
}

int main(int argc, char **argv) {
  int code = ProgramMain(argc, argv);
  if (IsWindowsHost()) ExitProcess(static_cast<uint32_t>(code));
  return code;
}
