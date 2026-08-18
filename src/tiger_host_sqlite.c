/* tiger_host_sqlite.c -- the SQLite that Leopard's SpeechDictionary wants.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit.
 *
 * Tiger's SpeechDictionary imports no SQLite at all: its dictionaries are the
 * flat SLPD and Cart files, and reading them is all it does.  Leopard's
 * imports eight sqlite3 entry points and keeps a real database alongside the
 * flat ones -- `Resources/Tuples`, 628736 bytes, "SQLite format 3" in its
 * first sixteen bytes, one table:
 *
 *     CREATE TABLE tuples (words TEXT PRIMARY KEY, flags INTEGER)
 *
 * 12891 rows of multi-word keys -- INTERNET_RATES, CAME_ALONG, JET_BOAT --
 * so it is the phrasing table: which neighbouring words bind together, and
 * how that changes stress and juncture.
 *
 * With no implementation these fell through to the generic stub, which
 * returns 0.  For sqlite3_open 0 is SQLITE_OK, so the dictionary believed it
 * had a database; for sqlite3_step 0 is *also* SQLITE_OK, where a row is
 * SQLITE_ROW (100).  Every lookup therefore reported "no match", silently and
 * consistently, and Leopard's phrasing quietly fell back to nothing.
 *
 * There is no need to carry a copy of SQLite to fix that.  Windows has
 * shipped `winsqlite3.dll` since Windows 10 1803, it exports the sqlite3_*
 * API, and there is a 32-bit build of it in SysWOW64 -- which
 * is the bitness this process is, because Apple's engine is i386 code.  So
 * bind to it at first use and forward.  Nothing of anyone's is redistributed:
 * Apple's database is the user's own file, and SQLite is already on the
 * machine.
 *
 * If the DLL is not there -- an older Windows -- everything below fails
 * honestly rather than cheerfully, and says so once.  An unimplemented
 * dictionary that reports success is the exact shape of the bug that cost a
 * night; see [[bugs-that-work-by-accident]].
 */
#define SQLITE_OK      0
#define SQLITE_ERROR   1
#define SQLITE_ROW   100
#define SQLITE_DONE  101

/* __stdcall, not __cdecl.  Upstream SQLite is cdecl, but Microsoft's build
 * defines SQLITE_APICALL as __stdcall on x86, and calling it as cdecl leaves
 * the arguments on the stack after every call: the engine faulted on the
 * first utterance rather than merely losing its phrasing.  The shims
 * themselves stay __cdecl, because that is the ABI Apple's engine calls us
 * with; only the pointers into the DLL are stdcall. */
typedef int  (__stdcall *p_open)(const char *, void **);
typedef int  (__stdcall *p_close)(void *);
typedef int  (__stdcall *p_prepare)(void *, const char *, int, void **,
                                    const char **);
typedef int  (__stdcall *p_bind_text)(void *, int, const char *, int, void *);
typedef int  (__stdcall *p_step)(void *);
typedef int  (__stdcall *p_column_int)(void *, int);
typedef int  (__stdcall *p_reset)(void *);
typedef int  (__stdcall *p_finalize)(void *);

static struct {
    int         tried, ok;
    p_open      open;
    p_close     close;
    p_prepare   prepare;
    p_bind_text bind_text;
    p_step      step;
    p_column_int column_int;
    p_reset     reset;
    p_finalize  finalize;
} g_sql;

static unsigned g_sql_opens, g_sql_rows;

static int sqlite_ready(void)
{
    HMODULE h;
    if (g_sql.tried) return g_sql.ok;
    g_sql.tried = 1;
    /* TIGER_SQLITE=0 turns the phrasing dictionary off, so the engine's
     * phrasing can be A/B'd against itself.  It exists because enabling this
     * table changed how sentences are broken up, and "is that better or
     * worse" is a question only a listener can answer -- but only if both
     * versions can be produced on demand. */
    { const char *e = getenv("TIGER_SQLITE");
      if (e && atoi(e) == 0) {
          fprintf(stderr, "tiger_host: phrasing dictionary disabled by "
                          "TIGER_SQLITE=0\n");
          return 0;
      } }
    h = LoadLibraryA("winsqlite3.dll");
    if (!h) {
        fprintf(stderr, "tiger_host: winsqlite3.dll is not on this system, so "
                        "Leopard's phrasing dictionary cannot be read -- "
                        "speech will work, with flatter phrasing\n");
        return 0;
    }
    g_sql.open       = (p_open)      GetProcAddress(h, "sqlite3_open");
    g_sql.close      = (p_close)     GetProcAddress(h, "sqlite3_close");
    g_sql.prepare    = (p_prepare)   GetProcAddress(h, "sqlite3_prepare");
    g_sql.bind_text  = (p_bind_text) GetProcAddress(h, "sqlite3_bind_text");
    g_sql.step       = (p_step)      GetProcAddress(h, "sqlite3_step");
    g_sql.column_int = (p_column_int)GetProcAddress(h, "sqlite3_column_int");
    g_sql.reset      = (p_reset)     GetProcAddress(h, "sqlite3_reset");
    g_sql.finalize   = (p_finalize)  GetProcAddress(h, "sqlite3_finalize");
    g_sql.ok = g_sql.open && g_sql.close && g_sql.prepare && g_sql.bind_text
            && g_sql.step && g_sql.column_int && g_sql.reset && g_sql.finalize;
    if (!g_sql.ok)
        fprintf(stderr, "tiger_host: winsqlite3.dll is missing entry points "
                        "this needs -- Leopard's phrasing dictionary will be "
                        "skipped\n");
    return g_sql.ok;
}

static int __cdecl sh_sqlite3_open(const char *path, void **db)
{
    int rc;
    if (db) *db = NULL;
    if (!sqlite_ready()) return SQLITE_ERROR;
    rc = g_sql.open(path, db);
    g_sql_opens++;
    if (g_verbose)
        printf("  [sql] open %s -> %d\n", path ? path : "(null)", rc);
    return rc;
}

static int __cdecl sh_sqlite3_prepare(void *db, const char *sql, int nbytes,
                                      void **stmt, const char **tail)
{
    if (stmt) *stmt = NULL;
    if (!sqlite_ready() || !db) return SQLITE_ERROR;
    if (g_verbose) printf("  [sql] prepare %s\n", sql ? sql : "(null)");
    return g_sql.prepare(db, sql, nbytes, stmt, tail);
}

static int __cdecl sh_sqlite3_bind_text(void *stmt, int idx, const char *val,
                                        int n, void *destructor)
{
    if (!sqlite_ready() || !stmt) return SQLITE_ERROR;
    return g_sql.bind_text(stmt, idx, val, n, destructor);
}

static int __cdecl sh_sqlite3_step(void *stmt)
{
    int rc;
    /* SQLITE_DONE, not SQLITE_OK: "the query finished and there were no rows"
     * is the honest answer when there is no database, and it is the one the
     * caller actually tests for. */
    if (!sqlite_ready() || !stmt) return SQLITE_DONE;
    rc = g_sql.step(stmt);
    if (rc == SQLITE_ROW) g_sql_rows++;
    return rc;
}

static int __cdecl sh_sqlite3_column_int(void *stmt, int col)
{
    if (!sqlite_ready() || !stmt) return 0;
    return g_sql.column_int(stmt, col);
}

static int __cdecl sh_sqlite3_reset(void *stmt)
{
    if (!sqlite_ready() || !stmt) return SQLITE_OK;
    return g_sql.reset(stmt);
}

static int __cdecl sh_sqlite3_finalize(void *stmt)
{
    if (!sqlite_ready() || !stmt) return SQLITE_OK;
    return g_sql.finalize(stmt);
}

static int __cdecl sh_sqlite3_close(void *db)
{
    if (!sqlite_ready() || !db) return SQLITE_OK;
    return g_sql.close(db);
}
