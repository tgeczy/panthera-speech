/* Parity check for the abbreviation rules against pantheraabbrev.py --
 * that module's tests are the spec; every case here mirrors one of them.
 * Compiled as a console exe that #includes the DLL source, so the rules
 * under test are the rules that ship, not a copy. */
#include "panthera_sapi.cpp"
#include <cstdio>

struct Case { const wchar_t *in; bool expand; const wchar_t *want; };

static const Case CASES[] = {
    /* despelling, setting off */
    {L"DR.", false, L"D R."},
    {L"ST. LOUIS", false, L"S T. LOUIS"},
    {L"the DR and ST fields", false, L"the D R and S T fields"},
    {L"Dr. Kirk", false, L"D R. Kirk"},
    {L"Dr Kirk", false, L"D R Kirk"},
    {L"Mrs. Kirk", false, L"M R S. Kirk"},
    {L"Prof. Kirk", false, L"P R O F. Kirk"},
    {L"Main Blvd.", false, L"Main B L V D."},
    {L"John Smith Jr. spoke", false, L"John Smith J R. spoke"},
    {L"4m", false, L"4 M"},
    {L"4 m", false, L"4 M"},
    {L"4mm", false, L"4 M M"},
    {L"12km", false, L"12 K M"},
    {L"4kg", false, L"4 K G"},
    {L"4mph", false, L"4mph"},
    {L"M4", false, L"M4"},
    {L"Ali vs Frazier", false, L"Ali vs Frazier"},
    {L"ali dr frazier", false, L"ali dr frazier"},
    {L"He fought in World War II.", false, L"He fought in World War I I."},
    {L"MIX", false, L"MIX"},
    {L"CIVIL", false, L"CIVIL"},
    {L"DRUM ADR CTRL FTP", false, L"DRUM ADR CTRL FTP"},
    {L"chapter ii and iii", false, L"chapter ii and iii"},
    /* the wrong-guess rewrites, both settings */
    {L"Space X's own page", false, L"Space ex's own page"},
    {L"Space X's own page", true, L"Space ex's own page"},
    {L"Space X\x2019s own page", true, L"Space ex\x2019s own page"},
    {L"Phycologist Dr. Kirk spoke", true, L"Phycologist Doctor Kirk spoke"},
    {L"Mulholland Dr. is long", true, L"Mulholland Dr. is long"},
    {L"Smith Dr., Springfield", true, L"Smith Dr., Springfield"},
    {L"turn onto Smith Dr.", true, L"turn onto Smith Dr."},
    /* with expansion on, nothing despells */
    {L"Dr. Kirk and 4mm of DR", true, L"Doctor Kirk and 4mm of DR"},
};

int wmain() {
    int failed = 0;
    for (const Case &c : CASES) {
        std::wstring t = c.in;
        disambiguate(t, c.expand);
        if (!c.expand) despell(t);
        if (t != c.want) {
            failed++;
            fwprintf(stderr, L"FAIL %ls (expand=%d)\n  want %ls\n  got  %ls\n",
                     c.in, c.expand ? 1 : 0, c.want, t.c_str());
        }
    }
    if (!failed) wprintf(L"all %u cases pass\n",
                         (unsigned)(sizeof CASES / sizeof CASES[0]));
    return failed ? 1 : 0;
}
