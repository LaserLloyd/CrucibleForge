/*
 * cf-jail: run one model-written program as an unprivileged user with no network (CrucibleForge container image).
 *
 * Installed setuid root in the image (/usr/local/libexec/cf-jail). The benchmark itself runs as the data folder's
 * owner; every graded program goes through this helper (crucibleforge/jail.py), which:
 *   1. hands the program's scratch directory (cwd, and any argument under the scratch root) to the jail user, mode
 *      a+rwX, so the program can work there and the benchmark can still read and clean it;
 *   2. sets resource limits (processes, data size, CPU seconds, file size, open files, no core files);
 *   3. drops to the jail user (65534:65534, no supplementary groups), irreversibly;
 *   4. dies with its parent, sets no_new_privs and a seccomp filter that refuses IPv4/IPv6/packet/netlink sockets,
 *      io_uring, ptrace and cross-process memory access, and refuses any non-native syscall ABI;
 *   5. execs the program with umask 0 (its files stay readable and removable by the benchmark).
 * Whoever calls it can only ever become the jail user: it takes no uid, no path to open, nothing to keep.
 * Any step that fails exits 126 with one line on stderr (fail closed: the program never runs unjailed).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <ftw.h>
#include <grp.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define JAIL_UID 65534
#define JAIL_GID 65534
#ifndef SCRATCH_ROOT
#define SCRATCH_ROOT "/tmp/cfwork/"
#endif

#if defined(__x86_64__)
#define ARCH_NR AUDIT_ARCH_X86_64
#elif defined(__aarch64__)
#define ARCH_NR AUDIT_ARCH_AARCH64
#else
#error "unsupported architecture"
#endif

static void die(const char *what) {
    fprintf(stderr, "cf-jail: %s: %s\n", what, strerror(errno));
    _exit(126);
}

static int give(const char *path, const struct stat *st, int flag, struct FTW *ftw) {
    (void)flag; (void)ftw;
    if (S_ISLNK(st->st_mode)) return 0;                  /* never follow or chown a link */
    if (lchown(path, JAIL_UID, JAIL_GID) != 0) die("chown scratch");
    mode_t m = st->st_mode & 07777;
    m |= S_ISDIR(st->st_mode) ? 0777 : 0666;
    if ((st->st_mode & 0111) != 0) m |= 0111;
    if (chmod(path, m) != 0) die("chmod scratch");
    return 0;
}

/* The top directory under the scratch root that holds `p` (e.g. /tmp/cfwork/tmpab12), or 0 when `p` is not under it. */
static int scratch_top(const char *p, char *out, size_t n) {
    char real[4096];
    if (!realpath(p, real)) return 0;
    size_t r = strlen(SCRATCH_ROOT);
    if (strncmp(real, SCRATCH_ROOT, r) != 0 || real[r] == 0) return 0;
    const char *slash = strchr(real + r, '/');
    size_t len = slash ? (size_t)(slash - real) : strlen(real);
    if (len + 1 > n) return 0;
    memcpy(out, real, len);
    out[len] = 0;
    return 1;
}

static void give_scratch(int argc, char **argv) {
    char cwd[4096], top[4096];
    if (getcwd(cwd, sizeof cwd) && scratch_top(cwd, top, sizeof top))
        if (nftw(top, give, 32, FTW_PHYS) != 0) die("scratch");
    for (int i = 1; i < argc; i++)
        if (argv[i][0] == '/' && scratch_top(argv[i], top, sizeof top))
            if (nftw(top, give, 32, FTW_PHYS) != 0) die("scratch");
}

static void limit(int res, rlim_t v) {
    struct rlimit cur;
    if (getrlimit(res, &cur) == 0 && cur.rlim_max != RLIM_INFINITY && cur.rlim_max < v) v = cur.rlim_max;
    struct rlimit rl = {v, v};
    if (setrlimit(res, &rl) != 0) die("setrlimit");
}

#define DENY(e) BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | ((e) & SECCOMP_RET_DATA))
#define ALLOW BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)
#define NR_IS(nr, skip_to_deny) BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (nr), 0, 1), DENY(EPERM)

static void seccomp_net_off(void) {
    struct sock_filter f[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, ARCH_NR, 1, 0),
        DENY(EPERM),                                                       /* a foreign syscall ABI */
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
#if defined(__x86_64__)
        BPF_JUMP(BPF_JMP | BPF_JGE | BPF_K, 0x40000000, 0, 1),           /* the x32 ABI */
        DENY(EPERM),
#endif
        NR_IS(__NR_io_uring_setup, 0),
        NR_IS(__NR_io_uring_enter, 0),
        NR_IS(__NR_io_uring_register, 0),
        NR_IS(__NR_ptrace, 0),
        NR_IS(__NR_process_vm_readv, 0),
        NR_IS(__NR_process_vm_writev, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_socket, 1, 0),
        ALLOW,
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[0])),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AF_UNIX, 0, 1),
        ALLOW,                                                             /* local pipes/socketpairs only */
        DENY(EACCES),
    };
    struct sock_fprog prog = {(unsigned short)(sizeof f / sizeof f[0]), f};
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) die("no_new_privs");
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog) != 0) die("seccomp");
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: cf-jail PROGRAM [ARGS...]\n");
        return 126;
    }
    if (geteuid() != 0) {
        errno = EPERM;
        die("not installed setuid root (or the container was started with no-new-privileges)");
    }
    give_scratch(argc, argv);
    /* The benchmark must be able to stop the program (a grader's timeout kills the process it started), but it
     * cannot signal another user's process. So this helper stays as the parent, killable by its caller, relays
     * signals to the jailed child and its exit status back; the child dies with it (PDEATHSIG). */
    pid_t pid = fork();
    if (pid < 0) die("fork");
    if (pid > 0) {
        static int sigs[] = {SIGTERM, SIGINT, SIGHUP, SIGQUIT};
        sigset_t all;
        sigemptyset(&all);
        for (size_t i = 0; i < sizeof sigs / sizeof sigs[0]; i++) sigaddset(&all, sigs[i]);
        sigprocmask(SIG_BLOCK, &all, NULL);
        for (;;) {
            int status;
            pid_t w = waitpid(pid, &status, WNOHANG);
            if (w == pid) {
                if (WIFEXITED(status)) _exit(WEXITSTATUS(status));
                if (WIFSIGNALED(status)) _exit(128 + WTERMSIG(status));
            } else if (w < 0 && errno != EINTR) {
                die("wait");
            }
            struct timespec ts = {0, 50 * 1000 * 1000};
            siginfo_t si;
            int s = sigtimedwait(&all, &si, &ts);
            if (s > 0) kill(pid, s);
        }
    }
    setsid();
    limit(RLIMIT_NPROC, 512);
    limit(RLIMIT_DATA, (rlim_t)8 << 30);
    limit(RLIMIT_CPU, 900);
    limit(RLIMIT_FSIZE, (rlim_t)1 << 30);
    limit(RLIMIT_NOFILE, 1024);
    limit(RLIMIT_CORE, 0);
    gid_t none[1] = {JAIL_GID};
    if (setgroups(1, none) != 0) die("setgroups");
    if (setresgid(JAIL_GID, JAIL_GID, JAIL_GID) != 0) die("setgid");
    if (setresuid(JAIL_UID, JAIL_UID, JAIL_UID) != 0) die("setuid");
    if (setresuid(-1, 0, -1) == 0 || geteuid() == 0) {               /* must be irreversible */
        errno = EPERM;
        die("privileges not dropped");
    }
    if (prctl(PR_SET_PDEATHSIG, SIGKILL, 0, 0, 0) != 0) die("pdeathsig");
    if (getppid() == 1) _exit(126);                                   /* the helper parent is already gone */
    seccomp_net_off();
    umask(0);
    execvp(argv[1], argv + 1);
    die("exec");
    return 126;
}
