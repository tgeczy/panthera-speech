#pragma once
#include <string>
#include <vector>

namespace panthera_sapi {
std::string utf8(const std::wstring &text);
void disambiguate(std::wstring &text, bool expand);
void despell(std::wstring &text);
std::wstring prepare_text(const std::wstring &text, bool commands,
                          bool expand, const std::wstring &generation);
/* Remove every embedded command from `text`, moving each position in
 * `positions` back by what was removed before it -- so a bookmark keeps
 * pointing at the same word once the document's own commands are gone. */
std::wstring strip_commands(const std::wstring &text, std::vector<size_t> &positions);
}
