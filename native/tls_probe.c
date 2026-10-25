#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>
int main(void)
{
    struct rlimit lim = {0, 0};
    setrlimit(RLIMIT_CORE, &lim);
    void *oc = dlopen("libopenconnect.so.5", RTLD_NOW);
    if (!oc) return 2;
    int (*init)(void) = dlsym(oc, "openconnect_init_ssl");
    if (!init || init() != 0) return 3;
    void *tls = dlopen("libgnutls.so.30", RTLD_NOW);
    if (!tls) return 6;
    int (*allocate)(void **) = dlsym(tls, "gnutls_certificate_allocate_credentials");
    int (*trust)(void *) = dlsym(tls, "gnutls_certificate_set_x509_system_trust");
    void (*release)(void *) = dlsym(tls, "gnutls_certificate_free_credentials");
    void *credentials = NULL;
    if (!allocate || !trust || !release || allocate(&credentials) != 0) return 7;
    int ca_count = trust(credentials);
    release(credentials);
    if (ca_count <= 0) return 8;
    printf("system_ca_count=%d\n", ca_count);
    FILE *maps = fopen("/proc/self/maps", "r");
    if (!maps) return 4;
    char line[4096];
    int loaded = 0;
    while (fgets(line, sizeof(line), maps))
        if (strstr(line, "libakisp11")) loaded = 1;
    fclose(maps);
    printf("akis_loaded=%d\n", loaded);
    return loaded ? 5 : 0;
}
