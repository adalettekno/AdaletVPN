#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

__attribute__((constructor)) static void tunnel_only(void)
{
    char path[4096];
    ssize_t n = readlink("/proc/self/exe", path, sizeof(path) - 1);
    if (n < 0 || n == sizeof(path) - 1) _exit(125);
    path[n] = 0;
    const char *name = strrchr(path, '/');
    name = name ? name + 1 : path;

    if (strcmp(name, "openconnect") != 0) return;

    void *tls = dlopen("libgnutls.so.30", RTLD_NOW | RTLD_NOLOAD);
    if (!tls) _exit(125);
    int (*init)(unsigned, const char *) = dlsym(tls, "gnutls_pkcs11_init");
    if (!init || init(0, NULL) != 0) _exit(125);

    unsetenv("LD_PRELOAD");
}
