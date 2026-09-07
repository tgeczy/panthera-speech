/* Small dense linear algebra for Snow Leopard's speech-unit cost weights.
 * Included by tiger_host.c. No engine data or vendor implementation is used.
 * ABI reference: https://www.netlib.org/lapack/lapack-3.1.1/html/ssyevr.f.html
 *
 * SSYEVR's interface is implemented with cyclic Jacobi rotations, in double
 * precision, rather than LAPACK's tridiagonal solver. The observed problem
 * is 3 by 3; retaining the general symmetric-matrix interface also lets the
 * tests check residuals and orthogonality on larger and degenerate problems.
 */
#include <float.h>
#include <limits.h>
static void __cdecl sh_sgemm(int order, int ta, int tb, int m, int n, int k,
                            unsigned alphabits, const float *a, int lda,
                            const float *b, int ldb, unsigned betabits,
                            float *c, int ldc)
{
    int i, j, h;
    float alpha = GFLOAT(alphabits), beta = GFLOAT(betabits);
    int row = order == CBLAS_ROWMAJOR;
    if (m <= 0 || n <= 0) return;
    for (i = 0; i < m; i++) for (j = 0; j < n; j++) {
        double sum = 0;
        size_t ci = row ? (size_t)i * ldc + j : (size_t)j * ldc + i;
        /* alpha/beta zero must not read unused (possibly NaN) operands. */
        if (alpha != 0) for (h = 0; h < k; h++) {
            int ar = ta == CBLAS_NOTRANS ? i : h;
            int ac = ta == CBLAS_NOTRANS ? h : i;
            int br = tb == CBLAS_NOTRANS ? h : j;
            int bc = tb == CBLAS_NOTRANS ? j : h;
            sum += (double)a[row ? (size_t)ar * lda + ac : (size_t)ac * lda + ar]
                         * b[row ? (size_t)br * ldb + bc : (size_t)bc * ldb + br];
        }
        c[ci] = (float)(alpha * sum + (beta == 0 ? 0 : (double)beta * c[ci]));
    }
}

static void __cdecl sh_ssyevr(const char *jobz, const char *range,
    const char *uplo, const int *np, float *a, const int *ldap,
    const float *vl, const float *vu, const int *il, const int *iu,
    const float *abstol, int *m, float *w, float *z, const int *ldzp,
    int *isuppz, float *work, const int *lwork, int *iwork,
    const int *liwork, int *info)
{
    int n = *np, lda = *ldap, ldz = *ldzp;
    int job = toupper((unsigned char)*jobz), r = toupper((unsigned char)*range);
    int upper = toupper((unsigned char)*uplo), query = *lwork == -1 || *liwork == -1;
    int i, j, p, q, sweep, lw, liw;
    size_t cells;
    double *d = NULL, *v = NULL, scale = 0;
    (void)abstol; /* Solve to double precision, tighter than the float output. */
    *info = 0;
    if (job != 'V' && job != 'N') *info = -1;
    else if (r != 'A' && r != 'I' && r != 'V') *info = -2;
    else if (upper != 'U' && upper != 'L') *info = -3;
    else if (n < 0 || n > INT_MAX / 26) *info = -4;
    else if (lda < (n > 1 ? n : 1)) *info = -6;
    else if (r == 'V' && n > 0 && *vu <= *vl) *info = -8;
    else if (r == 'I' && (*il < 1 || *il > (n > 1 ? n : 1))) *info = -9;
    else if (r == 'I' && (*iu < (n < *il ? n : *il) || *iu > n)) *info = -10;
    else if (ldz < 1 || (job == 'V' && ldz < n)) *info = -15;
    if (*info) return;
    lw = n ? 26 * n : 1; liw = n ? 10 * n : 1;
    work[0] = (float)lw; iwork[0] = liw;
    if (!query && *lwork < lw) *info = -18;
    else if (!query && *liwork < liw) *info = -20;
    if (*info || query) return;
    *m = 0;
    if (!n) return;
    if ((size_t)n > (size_t)-1 / sizeof(double) / (size_t)n) { *info = 1; return; }
    cells = (size_t)n * n;
    d = (double *)malloc(cells * sizeof(double));
    if (job == 'V') v = (double *)calloc(cells, sizeof(double));
    if (!d || (job == 'V' && !v)) { *info = 1; goto done; }
    for (i = 0; i < n; i++) for (j = 0; j < n; j++) {
        int ar = upper == 'U' ? (i < j ? i : j) : (i > j ? i : j);
        int ac = upper == 'U' ? (i > j ? i : j) : (i < j ? i : j);
        double x = a[(size_t)ac * lda + ar];
        if (!(x >= -FLT_MAX && x <= FLT_MAX)) { *info = 1; goto done; }
        d[(size_t)i * n + j] = x;
        if (fabs(x) > scale) scale = fabs(x);
    }
    if (v) for (i = 0; i < n; i++) v[(size_t)i * n + i] = 1;
    /* Scaling keeps subnormal and very large float matrices equally usable. */
    if (scale) for (size_t h = 0; h < cells; h++) d[h] /= scale;
    for (sweep = 0; sweep < 64; sweep++) {
        double off = 0;
        for (p = 0; p < n; p++) for (q = p + 1; q < n; q++) {
            double apq = d[(size_t)p * n + q], tau, t, c, s, app, aqq;
            if (fabs(apq) > off) off = fabs(apq);
            if (fabs(apq) <= 1e-15) continue;
            app = d[(size_t)p * n + p]; aqq = d[(size_t)q * n + q];
            tau = (aqq - app) / (2 * apq);
            t = (tau < 0 ? -1 : 1) / (fabs(tau) + sqrt(1 + tau * tau));
            c = 1 / sqrt(1 + t * t); s = t * c;
            for (i = 0; i < n; i++) if (i != p && i != q) {
                double x = d[(size_t)i * n + p], y = d[(size_t)i * n + q];
                d[(size_t)i * n + p] = d[(size_t)p * n + i] = c*x - s*y;
                d[(size_t)i * n + q] = d[(size_t)q * n + i] = s*x + c*y;
            }
            d[(size_t)p * n + p] = app - t * apq;
            d[(size_t)q * n + q] = aqq + t * apq;
            d[(size_t)p * n + q] = d[(size_t)q * n + p] = 0;
            if (v) for (i = 0; i < n; i++) {
                double x = v[(size_t)i * n + p], y = v[(size_t)i * n + q];
                v[(size_t)i * n + p] = c*x - s*y;
                v[(size_t)i * n + q] = s*x + c*y;
            }
        }
        if (off <= 1e-15) break;
    }
    if (sweep == 64) { *info = 1; goto done; }
    /* Sort eigenpairs before applying the one-based inclusive index range. */
    for (i = 0; i < n; i++) {
        int best = i;
        for (j = i + 1; j < n; j++)
            if (d[(size_t)j*n+j] < d[(size_t)best*n+best]) best = j;
        if (best != i) {
            double x = d[(size_t)i*n+i];
            d[(size_t)i*n+i] = d[(size_t)best*n+best]; d[(size_t)best*n+best] = x;
            if (v) for (j = 0; j < n; j++) {
                x = v[(size_t)j*n+i]; v[(size_t)j*n+i] = v[(size_t)j*n+best];
                v[(size_t)j*n+best] = x;
            }
        }
    }
    for (i = 0; i < n; i++) {
        double eig = d[(size_t)i*n+i] * scale;
        if (r == 'I' && (i + 1 < *il || i + 1 > *iu)) continue;
        if (r == 'V' && !(eig > *vl && eig <= *vu)) continue;
        w[*m] = (float)eig;
        if (v) {
            for (j = 0; j < n; j++) z[(size_t)*m * ldz + j] = (float)v[(size_t)j*n+i];
            isuppz[2 * *m] = 1; isuppz[2 * *m + 1] = n;
        }
        ++*m;
    }
done:
    free(d); free(v);
}

static int linalg_gemm_check(void)
{
    float *buf = (float *)GMEM_ALLOC(3 * 64 * sizeof(float));
    int row, ta, tb, zero, i, j;
    if (!buf) return 2;
    for (row = 0; row < 2; row++) for (ta = 0; ta < 2; ta++)
    for (tb = 0; tb < 2; tb++) for (zero = 0; zero < 2; zero++) {
        float *a = buf, *b = buf + 64, *c = buf + 128;
        float alpha = .75f, beta = zero ? 0 : -.5f;
        unsigned ab, bb;
        memcpy(&ab, &alpha, 4); memcpy(&bb, &beta, 4);
        for (i = 0; i < 192; i++) buf[i] = 777;
        for (i = 0; i < 2; i++) for (j = 0; j < 4; j++) {
            int ar = ta ? j : i, ac = ta ? i : j;
            a[row ? ar*7+ac : ac*7+ar] = (i*3+j-2)/4.0f;
        }
        for (i = 0; i < 4; i++) for (j = 0; j < 3; j++) {
            int br = tb ? j : i, bc = tb ? i : j;
            b[row ? br*7+bc : bc*7+br] = (i*2-j+1)/8.0f;
        }
        for (i = 0; i < 2; i++) for (j = 0; j < 3; j++) {
            unsigned nanbits = 0x7fc00000;
            float *at = &c[row ? i*7+j : j*7+i];
            if (zero) memcpy(at, &nanbits, 4); else *at = i + j/10.0f;
        }
#ifdef TIGER_UC
        {
            unsigned args[] = { row ? 101 : 102, ta ? 112 : 111, tb ? 112 : 111,
                2,3,4,ab,GP(a),7,GP(b),7,bb,GP(c),7 };
            uc_call(uc_bind_target("_cblas_sgemm", (void *)sh_sgemm), 14, args);
        }
#else
        sh_sgemm(row ? 101 : 102,ta ? 112 : 111,tb ? 112 : 111,
                 2,3,4,ab,a,7,b,7,bb,c,7);
#endif
        fprintf(stdout, "[linalg] gemm%d%d%d%d", row,ta,tb,zero);
        for (i = 0; i < 64; i++) fprintf(stdout, " %.9g", c[i]);
        fprintf(stdout, "\n");
    }
    GMEM_FREE(buf);
    return 0;
}

/* Text-only numerical oracle: generated inputs, no speech data required.
 * A UC build traverses the guest trampoline, including all 21 arguments. */
static int linalg_check(void)
{
    struct probe {
        int n, lda, ldz, il, iu, m, info, lw, liw;
        char job, range, uplo;
        float vl, vu, tol, a[32*32], w[32], z[32*32], work[26*32];
        int iw[10*32], support[64];
    } *p = (struct probe *)GMEM_ALLOC(sizeof(struct probe));
    int i, j, query;
    if (!p) return 2;
    if (linalg_gemm_check()) { GMEM_FREE(p); return 2; }
    memset(p, 0, sizeof *p);
    if (scanf("%d %c %c %c %d %d %f %f", &p->n, &p->job, &p->range,
              &p->uplo, &p->il, &p->iu, &p->vl, &p->vu) != 8 ||
        p->n < 0 || p->n > 32) { GMEM_FREE(p); return 2; }
    p->lda = p->ldz = p->n ? p->n : 1;
    for (i = 0; i < p->n; i++) for (j = 0; j < p->n; j++)
        if (scanf("%f", &p->a[j*p->lda+i]) != 1) { GMEM_FREE(p); return 2; }
    for (query = 1; query >= 0; query--) {
        p->lw = query ? -1 : (int)p->work[0];
        p->liw = query ? -1 : p->iw[0];
#ifdef TIGER_UC
        {
            unsigned args[] = { GP(&p->job),GP(&p->range),GP(&p->uplo),GP(&p->n),
                GP(p->a),GP(&p->lda),GP(&p->vl),GP(&p->vu),GP(&p->il),GP(&p->iu),
                GP(&p->tol),GP(&p->m),GP(p->w),GP(p->z),GP(&p->ldz),GP(p->support),
                GP(p->work),GP(&p->lw),GP(p->iw),GP(&p->liw),GP(&p->info) };
            uc_call(uc_bind_target("_ssyevr_", (void *)sh_ssyevr), 21, args);
        }
#else
        sh_ssyevr(&p->job,&p->range,&p->uplo,&p->n,p->a,&p->lda,&p->vl,&p->vu,
            &p->il,&p->iu,&p->tol,&p->m,p->w,p->z,&p->ldz,p->support,
            p->work,&p->lw,p->iw,&p->liw,&p->info);
#endif
        fprintf(stdout, "[linalg] %s %d %d %d\n", query ? "query" : "result",
               p->info, (int)p->work[0], p->iw[0]);
        if (p->info) break;
    }
    fprintf(stdout, "[linalg] values");
    for (i = 0; i < p->m; i++) fprintf(stdout, " %.9g", p->w[i]);
    fprintf(stdout, "\n[linalg] vectors");
    if (p->job == 'V') for (i = 0; i < p->n; i++) for (j = 0; j < p->m; j++)
        fprintf(stdout, " %.9g", p->z[j*p->ldz+i]);
    fprintf(stdout, "\n");
    GMEM_FREE(p);
    return 0;
}
