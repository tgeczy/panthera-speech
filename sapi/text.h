#pragma once
#include <string>

namespace panthera_sapi {
std::string utf8(const std::wstring &text);
void disambiguate(std::wstring &text, bool expand);
void despell(std::wstring &text);
std::wstring prepare_text(const std::wstring &text, bool commands,
                          bool expand, const std::wstring &generation);
}
