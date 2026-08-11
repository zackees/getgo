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
#include <fstream>
#include <string>
#include <unordered_set>
#include <vector>

extern char **environ;

#ifndef GETGO_VERSION
#define GETGO_VERSION "0.1.0"
#endif

namespace {

constexpr const char *kUsage = "Usage: getgo [--yes | --no-modify-path] <package> [<package>...]";
constexpr const char *kUnixInstaller = "https://astral.sh/uv/install.sh";
constexpr const char *kWindowsInstaller = "https://astral.sh/uv/install.ps1";
[[gnu::used]] constexpr const char kBuildMetadata[] =
    "getgo build toolchain: clang-tool-chain 1.5.8; cosmocc fat x86_64+aarch64";

bool IsWindowsHost() { return IsWindows(); }

enum class PathPolicy { kAuto, kYes, kNo };

bool EnvEnabled(const char *name) {
  const char *value = std::getenv(name);
  if (!value) return false;
  std::string lowered(value);
  for (char &c : lowered) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return lowered == "1" || lowered == "true" || lowered == "yes" || lowered == "on";
}

int DecodeStatus(int status) {
  // Cosmopolitan's NT waitpid backend returns the native Windows process exit
  // code directly rather than encoding it as a POSIX wait status.
  if (IsWindowsHost()) return status;
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

std::string Parent(const std::string &path) {
  size_t end = path.find_last_of("/\\");
  if (end == std::string::npos) return ".";
  if (end == 0) return path.substr(0, 1);
  return path.substr(0, end);
}

std::vector<std::string> ExecutableNames(const std::string &name) {
  if (!IsWindowsHost()) return {name};
  return {name + ".exe", name + ".com", name};
}

std::string FindOnPath(const std::string &name) {
  const char *path_value = std::getenv("PATH");
  if (!path_value) return {};
  // Cosmopolitan normalizes a native Windows PATH such as C:\one;D:\two to
  // its POSIX view (/C/one:/D/two) before exposing it through getenv().
  const char separator = ':';
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
  std::vector<std::string> directories;
  for (const char *name : {"UV_INSTALL_DIR", "UV_UNMANAGED_INSTALL", "XDG_BIN_HOME"}) {
    const char *value = std::getenv(name);
    if (value && *value) directories.emplace_back(value);
  }
  const char *xdg_data = std::getenv("XDG_DATA_HOME");
  if (xdg_data && *xdg_data) directories.push_back(Join(Parent(xdg_data), "bin"));
  const char *home = std::getenv(IsWindowsHost() ? "USERPROFILE" : "HOME");
  if (home && *home) directories.push_back(Join(Join(home, ".local"), "bin"));
  for (const std::string &directory : directories) {
    for (const std::string &executable : ExecutableNames("uv")) {
      std::string candidate = Join(directory, executable);
      if (IsFile(candidate)) return AbsolutePath(candidate);
    }
  }
  return {};
}

std::string FindPowerShell() {
  for (const char *name : {"powershell", "pwsh"}) {
    std::string found = FindOnPath(name);
    if (!found.empty()) return found;
  }
  const char *program_files = std::getenv("PROGRAMFILES");
  if (program_files && *program_files) {
    std::string candidate = Join(program_files, "PowerShell/7/pwsh.exe");
    if (IsFile(candidate)) return AbsolutePath(candidate);
  }
  const char *system_root = std::getenv("SYSTEMROOT");
  if (system_root && *system_root) {
    std::string candidate = Join(system_root, "System32/WindowsPowerShell/v1.0/powershell.exe");
    if (IsFile(candidate)) return AbsolutePath(candidate);
  }
  return {};
}

int BootstrapUv() {
  const char *old_value = std::getenv("UV_NO_MODIFY_PATH");
  const bool had_old_value = old_value != nullptr;
  const std::string saved_value = old_value ? old_value : "";
  (void)setenv("UV_NO_MODIFY_PATH", "1", 1);
  int result = 1;
  if (IsWindowsHost()) {
    std::string powershell = FindPowerShell();
    if (powershell.empty()) {
      std::fputs("getgo: PowerShell is required to install uv\n", stderr);
    } else {
      result = Spawn({powershell, "-ExecutionPolicy", "ByPass", "-c", std::string("irm ") + kWindowsInstaller + " | iex"});
    }
  } else {
    std::string curl = FindOnPath("curl");
    if (!curl.empty()) {
      result = SpawnPipeline({curl, "-LsSf", kUnixInstaller}, {"/bin/sh"});
    } else {
      std::string wget = FindOnPath("wget");
      if (!wget.empty()) {
        result = SpawnPipeline({wget, "-qO-", kUnixInstaller}, {"/bin/sh"});
      } else {
        std::fputs("getgo: curl or wget is required to install uv\n", stderr);
      }
    }
  }
  if (had_old_value) {
    (void)setenv("UV_NO_MODIFY_PATH", saved_value.c_str(), 1);
  } else {
    (void)unsetenv("UV_NO_MODIFY_PATH");
  }
  return result;
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
      if (c == '\\') c = '/';
      c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    // Treat Cosmopolitan's /C/foo view and Windows' C:\foo spelling as the
    // same directory when checking whether uv's tool bin is already on PATH.
    if (value.size() >= 2 && value[0] == '/' && std::isalpha(static_cast<unsigned char>(value[1])) &&
        (value.size() == 2 || value[2] == '/')) {
      value[0] = value[1];
      value[1] = ':';
    }
  }
  return value;
}

bool PathContains(const std::string &directory) {
  const char *path_value = std::getenv("PATH");
  if (!path_value) return false;
  const char separator = ':';
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

std::string ReadText(const std::string &path) {
  std::ifstream stream(path, std::ios::binary);
  if (!stream) return {};
  return std::string(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
}

bool MakeDirectories(const std::string &path) {
  if (path.empty()) return false;
  struct stat information;
  if (stat(path.c_str(), &information) == 0 && S_ISDIR(information.st_mode)) return true;
  for (size_t i = 1; i <= path.size(); ++i) {
    if (i != path.size() && path[i] != '/' && path[i] != '\\') continue;
    std::string part = path.substr(0, i);
    if (part.empty() || part == "/" || (part.size() == 2 && part[1] == ':')) continue;
    if (mkdir(part.c_str(), 0755) != 0 && errno != EEXIST) return false;
  }
  return true;
}

bool WriteText(const std::string &path, const std::string &content) {
  if (!MakeDirectories(Parent(path))) return false;
  std::ofstream stream(path, std::ios::binary | std::ios::trunc);
  if (!stream) return false;
  stream << content;
  return static_cast<bool>(stream);
}

std::string ShellQuote(const std::string &value) {
  std::string result = "'";
  for (char c : value) {
    if (c == '\'') {
      result += "'\"'\"'";
    } else {
      result += c;
    }
  }
  return result + "'";
}

bool AppendOnce(const std::string &path, const std::string &marker, const std::string &addition) {
  std::string existing = ReadText(path);
  if (existing.find(marker) != std::string::npos) return true;
  if (!MakeDirectories(Parent(path))) return false;
  std::ofstream stream(path, std::ios::binary | std::ios::app);
  if (!stream) return false;
  if (!existing.empty() && existing.back() != '\n') stream << '\n';
  stream << marker << '\n' << addition;
  if (addition.empty() || addition.back() != '\n') stream << '\n';
  return static_cast<bool>(stream);
}

std::vector<std::string> UniqueMissingPaths(const std::vector<std::string> &paths) {
  std::vector<std::string> result;
  std::unordered_set<std::string> seen;
  for (const std::string &path : paths) {
    if (path.empty()) continue;
    std::string normalized = NormalizePath(path);
    if (seen.insert(normalized).second && !PathContains(path)) result.push_back(path);
  }
  return result;
}

bool AppendGithubPath(const std::vector<std::string> &paths) {
  const char *destination = std::getenv("GITHUB_PATH");
  if (!destination || !*destination) return false;
  std::string existing = ReadText(destination);
  std::unordered_set<std::string> present;
  size_t start = 0;
  while (start <= existing.size()) {
    size_t end = existing.find('\n', start);
    if (end == std::string::npos) end = existing.size();
    std::string line = Trim(existing.substr(start, end - start));
    if (!line.empty()) present.insert(NormalizePath(line));
    if (end == existing.size()) break;
    start = end + 1;
  }
  std::ofstream stream(destination, std::ios::binary | std::ios::app);
  if (!stream) {
    std::fprintf(stderr, "getgo: could not update GITHUB_PATH: %s\n", std::strerror(errno));
    return false;
  }
  if (!existing.empty() && existing.back() != '\n') stream << '\n';
  for (const std::string &path : paths) {
    if (present.insert(NormalizePath(path)).second) stream << path << '\n';
  }
  return static_cast<bool>(stream);
}

std::string WindowsPath(std::string value) {
  if (value.size() >= 3 && value[0] == '/' && std::isalpha(static_cast<unsigned char>(value[1])) && value[2] == '/') {
    value = std::string(1, value[1]) + ":" + value.substr(2);
  }
  for (char &c : value) {
    if (c == '/') c = '\\';
  }
  return value;
}

std::string GitBashPath(std::string value) {
  for (char &c : value) {
    if (c == '\\') c = '/';
  }
  if (value.size() >= 3 && std::isalpha(static_cast<unsigned char>(value[0])) && value[1] == ':' && value[2] == '/') {
    value = "/" + std::string(1, static_cast<char>(std::tolower(static_cast<unsigned char>(value[0])))) + value.substr(2);
  } else if (value.size() >= 3 && value[0] == '/' && std::isalpha(static_cast<unsigned char>(value[1])) && value[2] == '/') {
    value[1] = static_cast<char>(std::tolower(static_cast<unsigned char>(value[1])));
  }
  return value;
}

void PrintActivation(const std::vector<std::string> &paths) {
  if (!IsWindowsHost()) {
    std::string joined;
    for (const std::string &path : paths) joined += (joined.empty() ? "" : ":") + path;
    std::printf("export PATH=\"%s:$PATH\"\n", joined.c_str());
    return;
  }
  std::string native;
  std::string bash;
  for (const std::string &path : paths) {
    native += (native.empty() ? "" : ";") + WindowsPath(path);
    bash += (bash.empty() ? "" : ":") + GitBashPath(path);
  }
  std::printf("PowerShell: $env:Path = \"%s;$env:Path\"\n", native.c_str());
  std::printf("Command Prompt: set \"PATH=%s;%%PATH%%\"\n", native.c_str());
  std::printf("Git Bash: export PATH=\"%s:$PATH\"\n", bash.c_str());
}

bool ConfigureUnixPath(const std::vector<std::string> &paths) {
  const char *home_value = std::getenv("HOME");
  if (!home_value || !*home_value) return false;
  std::string home(home_value);
  const char *xdg_value = std::getenv("XDG_CONFIG_HOME");
  std::string config = xdg_value && *xdg_value ? xdg_value : Join(home, ".config");
  std::string environment = Join(Join(config, "getgo"), "env");
  std::string content = "# Generated by getgo. Safe to source more than once.\n";
  for (auto it = paths.rbegin(); it != paths.rend(); ++it) {
    std::string quoted = ShellQuote(*it);
    content += "case \":$PATH:\" in *:" + quoted + ":*) ;;\n  *) export PATH=" + quoted + ":\"$PATH\" ;;\nesac\n";
  }
  if (!WriteText(environment, content)) return false;

  const char *shell_value = std::getenv("SHELL");
  std::string shell = shell_value && *shell_value ? std::string(shell_value) : "sh";
  size_t slash = shell.find_last_of("/\\");
  if (slash != std::string::npos) shell = shell.substr(slash + 1);
  const std::string marker = "# getgo PATH bootstrap";
  const std::string source = "[ -f " + ShellQuote(environment) + " ] && . " + ShellQuote(environment) + "\n";
  if (shell == "fish") {
    std::string fish = Join(Join(Join(config, "fish"), "conf.d"), "getgo.fish");
    std::string fish_content = "# Generated by getgo. Safe to source more than once.\n";
    for (const std::string &path : paths) fish_content += "fish_add_path --global --move " + ShellQuote(path) + "\n";
    return WriteText(fish, fish_content);
  }
  if (shell == "zsh") {
    const char *zdot = std::getenv("ZDOTDIR");
    return AppendOnce(Join(zdot && *zdot ? zdot : home, ".zshenv"), marker, source);
  }
  if (shell == "bash") {
    std::string login;
    for (const char *name : {".bash_profile", ".bash_login", ".profile"}) {
      std::string candidate = Join(home, name);
      if (!ReadText(candidate).empty() || access(candidate.c_str(), F_OK) == 0) {
        login = candidate;
        break;
      }
    }
    if (login.empty()) login = Join(home, ".bash_profile");
    return AppendOnce(login, marker, source) && AppendOnce(Join(home, ".bashrc"), marker, source);
  }
  if (shell == "ksh" || shell == "mksh") {
    return AppendOnce(Join(home, ".profile"), marker, source) && AppendOnce(Join(home, ".kshrc"), marker, source);
  }
  if (shell == "csh" || shell == "tcsh") {
    std::string additions;
    for (auto it = paths.rbegin(); it != paths.rend(); ++it) {
      std::string escaped;
      for (char c : *it) {
        if (c == '\\' || c == '"') escaped += '\\';
        escaped += c;
      }
      additions += "if ( \":${PATH}:\" !~ *\":" + escaped + ":\"* ) then\n"
                   "  setenv PATH \"" +
                   escaped + ":${PATH}\"\nendif\n";
    }
    return AppendOnce(Join(home, ".cshrc"), marker, additions);
  }
  if (shell == "nu" || shell == "nushell") {
    std::string additions;
    for (auto it = paths.rbegin(); it != paths.rend(); ++it) {
      std::string quoted = ShellQuote(*it);
      additions += "if not ($env.PATH | any { |entry| $entry == " + quoted + " }) {\n"
                   "  $env.PATH = ($env.PATH | prepend " +
                   quoted + ")\n}\n";
    }
    return AppendOnce(Join(Join(config, "nushell"), "env.nu"), marker, additions);
  }
  return AppendOnce(Join(home, ".profile"), marker, source);
}

std::string PowerShellQuote(const std::string &value) {
  std::string result = "'";
  for (char c : value) result += c == '\'' ? "''" : std::string(1, c);
  return result + "'";
}

bool ConfigureWindowsPath(const std::vector<std::string> &paths) {
  const char *test_file = std::getenv("_GETGO_TEST_WINDOWS_PATH_FILE");
  if (test_file && *test_file) {
    std::string existing = ReadText(test_file);
    std::unordered_set<std::string> present;
    size_t start = 0;
    while (start <= existing.size()) {
      size_t end = existing.find(';', start);
      if (end == std::string::npos) end = existing.size();
      std::string item = existing.substr(start, end - start);
      if (!item.empty()) present.insert(NormalizePath(item));
      if (end == existing.size()) break;
      start = end + 1;
    }
    std::string additions;
    for (const std::string &path : paths) {
      std::string native = WindowsPath(path);
      if (present.insert(NormalizePath(native)).second) additions += (additions.empty() ? "" : ";") + native;
    }
    if (additions.empty()) return true;
    std::string result = additions + (existing.empty() ? "" : ";" + existing);
    return WriteText(test_file, result);
  }
  std::string powershell = FindPowerShell();
  if (powershell.empty()) return false;
  std::string values;
  for (const std::string &path : paths) values += (values.empty() ? "" : ",") + PowerShellQuote(WindowsPath(path));
  std::string command =
      "$p=[Environment]::GetEnvironmentVariable('Path','User');$a=@(" + values +
      ");$v=@($p-split ';'|?{$_});foreach($d in $a){if($v-notcontains$d){$v=@($d)+$v}};"
      "[Environment]::SetEnvironmentVariable('Path',($v-join';'),'User')";
  return Spawn({powershell, "-NoProfile", "-NonInteractive", "-Command", command}) == 0;
}

bool WantsPathSetup(PathPolicy policy, const std::vector<std::string> &paths) {
  if (policy == PathPolicy::kYes) return true;
  if (policy == PathPolicy::kNo || !isatty(STDIN_FILENO)) return false;
  std::string joined;
  for (const std::string &path : paths) joined += (joined.empty() ? "" : ", ") + path;
  std::fprintf(stderr, "getgo: add %s to PATH for future shells? [Y/n] ", joined.c_str());
  std::fflush(stderr);
  char answer[32];
  if (!std::fgets(answer, sizeof(answer), stdin)) return false;
  std::string value = Trim(answer);
  for (char &c : value) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return value.empty() || value == "y" || value == "yes";
}

int ProbeUvShellWithoutUnixEdits(const std::string &uv, const std::string &tool_bin) {
  if (IsWindowsHost()) return Spawn({uv, "tool", "update-shell"});
  const char *old_value = std::getenv("PATH");
  const bool had_old_value = old_value != nullptr;
  const std::string saved_value = old_value ? old_value : "";
  const std::string probe_path = tool_bin + ":" + saved_value;
  (void)setenv("PATH", probe_path.c_str(), 1);
  int result = Spawn({uv, "tool", "update-shell"});
  if (had_old_value) {
    (void)setenv("PATH", saved_value.c_str(), 1);
  } else {
    (void)unsetenv("PATH");
  }
  return result;
}

void FinishPathSetup(const std::string &uv, PathPolicy policy) {
  std::string output;
  int directory_code = Spawn({uv, "tool", "dir", "--bin"}, -1, &output);
  std::string tool_bin = directory_code == 0 ? Trim(output) : std::string();
  std::vector<std::string> missing = UniqueMissingPaths({Parent(uv), tool_bin});
  if (missing.empty()) return;

  const char *github_path = std::getenv("GITHUB_PATH");
  if (policy != PathPolicy::kNo && github_path && *github_path && AppendGithubPath(missing)) {
    std::fputs("getgo: PATH updated for subsequent GitHub Actions steps\n", stderr);
    PrintActivation(missing);
    return;
  }
  if (!WantsPathSetup(policy, missing)) {
    std::fputs("getgo: installed successfully, but an executable directory is not on PATH\n", stderr);
    PrintActivation(missing);
    return;
  }

  std::vector<std::string> remaining = missing;
  bool tool_missing = false;
  if (!tool_bin.empty()) {
    for (const std::string &path : missing) tool_missing = tool_missing || NormalizePath(path) == NormalizePath(tool_bin);
  }
  bool configured = false;
  if (!IsWindowsHost()) {
    if (tool_missing) (void)ProbeUvShellWithoutUnixEdits(uv, tool_bin);
    configured = ConfigureUnixPath(remaining);
  } else {
    if (tool_missing && ProbeUvShellWithoutUnixEdits(uv, tool_bin) == 0) {
      std::vector<std::string> filtered;
      for (const std::string &path : remaining) {
        if (NormalizePath(path) != NormalizePath(tool_bin)) filtered.push_back(path);
      }
      remaining = filtered;
    }
    configured = remaining.empty() || ConfigureWindowsPath(remaining);
  }
  std::fputs(configured ? "getgo: PATH configured for future shells; open a new shell to use installed tools\n"
                        : "getgo: installed successfully, but automatic PATH setup failed\n",
             stderr);
  PrintActivation(missing);
}

}  // namespace

int ProgramMain(int argc, char **argv) {
  if (argc == 2 && std::strcmp(argv[1], "--help") == 0) {
    std::printf(
        "%s\nInstall PyPI packages as persistent uv tools with managed Python.\n"
        "  --yes             Add missing executable directories to future shells.\n"
        "  --no-modify-path  Never modify shell startup files or the user PATH.\n",
        kUsage);
    return 0;
  }
  if (argc == 2 && std::strcmp(argv[1], "--version") == 0) {
    std::printf("getgo %s\n", GETGO_VERSION);
    return 0;
  }
  if (argc < 2) return UsageError("at least one package is required");
  bool yes = EnvEnabled("GETGO_YES");
  bool no_modify = EnvEnabled("GETGO_NO_MODIFY_PATH");
  std::vector<std::string> packages;
  for (int i = 1; i < argc; ++i) {
    if (std::strcmp(argv[i], "--yes") == 0) {
      yes = true;
      continue;
    }
    if (std::strcmp(argv[i], "--no-modify-path") == 0) {
      no_modify = true;
      continue;
    }
    if (argv[i][0] == '-') return UsageError(std::string("unsupported option: ") + argv[i]);
    if (!IsPackageName(argv[i])) return UsageError(std::string("invalid PyPI distribution name: ") + argv[i]);
    packages.emplace_back(argv[i]);
  }
  if (yes && no_modify) return UsageError("--yes and --no-modify-path are mutually exclusive");
  if (packages.empty()) return UsageError("at least one package is required");
  PathPolicy path_policy = yes ? PathPolicy::kYes : no_modify ? PathPolicy::kNo : PathPolicy::kAuto;

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

  for (const std::string &package : packages) {
    int code = Spawn({uv, "tool", "install", "--managed-python", package + "@latest"});
    if (code) return code;
  }
  FinishPathSetup(uv, path_policy);
  return 0;
}

int main(int argc, char **argv) {
  int code = ProgramMain(argc, argv);
  if (IsWindowsHost()) ExitProcess(static_cast<uint32_t>(code));
  return code;
}
