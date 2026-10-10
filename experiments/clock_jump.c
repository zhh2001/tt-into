/* 仅在指定交换机进程内改变 CLOCK_REALTIME，宿主时钟不变。 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

static int (*original)(clockid_t, struct timespec *);
static pthread_once_t once = PTHREAD_ONCE_INIT;
static int64_t origin_ns;
static _Atomic long last_bucket = -1;
static void initialize(void) {
    original = dlsym(RTLD_NEXT, "clock_gettime");
    if (!original) _exit(127);
    struct timespec t;
    original(CLOCK_MONOTONIC, &t);
    origin_ns = (int64_t)t.tv_sec * 1000000000LL + t.tv_nsec;
}
int clock_gettime(clockid_t which, struct timespec *value) {
    pthread_once(&once, initialize);
    int rc = original(which, value);
    if (rc || which != CLOCK_REALTIME) return rc;
    struct timespec t;
    original(CLOCK_MONOTONIC, &t);
    int64_t elapsed = (int64_t)t.tv_sec * 1000000000LL + t.tv_nsec - origin_ns;
    if (elapsed < 100000000LL) return rc;
    long bucket = elapsed / 200000000LL;
    long shift = bucket % 2 ? 3600 : -3600;
    value->tv_sec += shift;
    long previous = atomic_load(&last_bucket);
    if (previous != bucket && atomic_compare_exchange_strong(&last_bucket, &previous, bucket)) {
        const char *log = getenv("TT_CLOCK_JUMP_LOG");
        if (log) {
            int fd = open(log, O_WRONLY | O_CREAT | O_APPEND, 0600);
            if (fd >= 0) {
                char line[128];
                int n = snprintf(line, sizeof(line), "%ld %lld %ld\n", (long)getpid(), (long long)elapsed, shift);
                (void)write(fd, line, n);
                close(fd);
            }
        }
    }
    return rc;
}
