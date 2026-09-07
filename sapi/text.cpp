#include "text.h"
#include <windows.h>
#include <cwctype>
#include <regex>

namespace panthera_sapi {
std::string utf8(const std::wstring &s) {
    int n=WideCharToMultiByte(CP_UTF8,0,s.data(),(int)s.size(),0,0,0,0);
    std::string r(n,0); if(n) WideCharToMultiByte(CP_UTF8,0,s.data(),(int)s.size(),&r[0],n,0,0); return r;
}
/* The abbreviation rules, ported from pantheraabbrev.py -- that module and
 * its tests are the spec; nothing here decides anything the Python side has
 * not measured.  Authored without lookbehind on both sides, because
 * std::wregex has none.
 *
 * `regex_replace` cannot compute a replacement, so the spaced-letters
 * rewrites walk matches by hand. */
static void spell_out(std::wstring &t, const std::wregex &re, bool upper) {
    std::wstring out; out.reserve(t.size()+8);
    auto it=std::wsregex_iterator(t.begin(),t.end(),re), end=std::wsregex_iterator();
    size_t last=0;
    for(;it!=end;++it){
        out.append(t,last,it->position(1)-last);
        const std::wstring tok=it->str(1);
        for(size_t j=0;j<tok.size();++j){
            if(j)out.push_back(L' ');
            out.push_back(upper?towupper(tok[j]):tok[j]);
        }
        last=it->position(1)+it->length(1);
    }
    out.append(t,last,std::wstring::npos);
    t.swap(out);
}

/* The engine's measured wrong guesses, settled whichever way the setting
 * points: "<proper noun> Dr." read as a street, and "X's" after a
 * camel-case split read as the roman numeral ("SpaceX's" was
 * "space ten's").  The Doctor rewrite only with expansion on -- with it
 * off, despelling reads "Dr." as letters and writing "Doctor" would be an
 * expansion the user declined. */
void disambiguate(std::wstring &t, bool expand) {
    static const std::wregex ex(L"\\bX(['\x2019]s)\\b");
    t=std::regex_replace(t,ex,L"ex$1");
    if(expand){
        static const std::wregex doc(L"\\bDr\\.(\\s+)(?=[A-Z][a-z])");
        t=std::regex_replace(t,doc,L"Doctor$1");
    }
}

/* "Expand abbreviations" off: the engine's own lexicon expands DR, Dr.,
 * St., and on 10.7 digit-adjacent units, none of which TIGER_NO_ABBREV
 * reaches -- so the abbreviation-shaped forms despell in the text.
 * Case-sensitive exactly as the Python side: lowercase prose ("vs",
 * "etc", "dr") is never touched. */
void despell(std::wstring &t) {
    static const std::wregex acronyms(
        L"\\b(CT|DR|ETC|FT|JR|MRS?|RD|SR|ST|VS)\\b");
    static const std::wregex titles(
        L"\\b(Blvd|Capt|Prof|Mrs|Ave|Gen|Gov|Rep|Sen|Ct|Dr|Ft|Jr|Lt|Mr|Ms"
        L"|Rd|Sr|St)\\b");
    static const std::wregex units(L"\\b(\\d+) ?(mm|cm|km|kg|g|m)\\b");
    static const std::wregex roman(
        L"\\b(?=[MDCLXVI]{2,}\\b)"
        L"(M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))\\b");
    spell_out(t,acronyms,false);
    spell_out(t,titles,true);
    {   /* units keep their number: "4mm" -> "4 M M" */
        std::wstring out; out.reserve(t.size()+8);
        auto it=std::wsregex_iterator(t.begin(),t.end(),units), end=std::wsregex_iterator();
        size_t last=0;
        for(;it!=end;++it){
            out.append(t,last,it->position(0)-last);
            out.append(it->str(1)); out.push_back(L' ');
            const std::wstring u=it->str(2);
            for(size_t j=0;j<u.size();++j){
                if(j)out.push_back(L' ');
                out.push_back(towupper(u[j]));
            }
            last=it->position(0)+it->length(0);
        }
        out.append(t,last,std::wstring::npos);
        t.swap(out);
    }
    {   /* MIX is M+IX, 1009, and the one English word the strict pattern
         * claims; everything else spaced out is the setting keeping its
         * word.  See the Python module for the whole argument. */
        std::wstring out; out.reserve(t.size()+8);
        auto it=std::wsregex_iterator(t.begin(),t.end(),roman), end=std::wsregex_iterator();
        size_t last=0;
        for(;it!=end;++it){
            out.append(t,last,it->position(1)-last);
            const std::wstring tok=it->str(1);
            if(tok==L"MIX")out.append(tok);
            else for(size_t j=0;j<tok.size();++j){
                if(j)out.push_back(L' ');
                out.push_back(tok[j]);
            }
            last=it->position(1)+it->length(1);
        }
        out.append(t,last,std::wstring::npos);
        t.swap(out);
    }
}

/* Match the NVDA/Android command boundary, including spaced delimiters.
 * Only prose passes through lexical rewrites; command payloads stay intact. */
std::wstring prepare_text(const std::wstring &text, bool commands,
                                 bool expand, const std::wstring &generation) {
    static const std::wregex command(L"\\[\\s*\\[([^\\]]{0,64})\\]\\s*\\]");
    static const std::wregex input(L"\\s*inpt\\s+[A-Za-z]{0,16}\\s*",
                                   std::regex_constants::icase);
    std::wstring out; size_t at=0;
    auto prose=[&](size_t end){
        std::wstring part=text.substr(at,end-at);
        disambiguate(part,expand);
        if(!expand)despell(part);
        out+=part;
    };
    for(auto i=std::wsregex_iterator(text.begin(),text.end(),command);
        i!=std::wsregex_iterator();++i){
        prose(i->position());
        if(commands&&(_wcsicmp(generation.c_str(),L"lion")!=0||
                      !std::regex_match(i->str(1),input)))
            out+=L"[["+i->str(1)+L"]]";
        at=i->position()+i->length();
    }
    prose(text.size());
    return out;
}


} // namespace panthera_sapi
