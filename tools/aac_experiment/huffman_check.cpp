// Compiled against the fetched experimental source, which supplies its tables.
// The reference follows child links one bit at a time, with a 24-bit bound.
#include "aac_decoder.cpp"
#include <cstdio>

int main() {
    using namespace glint::aac;
    build_trees();
    uint32_t random = 0xb6420ca1u;
    unsigned checked = 0;
    for (int iteration = 0; iteration < 400000; ++iteration) {
        random ^= random << 13; random ^= random >> 17; random ^= random << 5;
        uint8_t data[4];
        std::memcpy(data, &random, sizeof(data));
        int len = iteration % 5;
        int offset = (iteration / 5) % 8;
        for (int book = 0; book < 12; ++book) {
            const auto &tree = book == 11 ? g_scf : g_spec[book];
            BitReader reference(data, len), fast(data, len);
            reference.pos = fast.pos = offset;
            int node = 0, expected = -1;
            for (int depth = 0; depth < 24; ++depth) {
                int next = tree.child[node][reference.get1()];
                if (next <= 0) { expected = ~next; break; }
                node = next;
            }
            int actual = tree.decode(fast);
            if (actual != expected || fast.pos != reference.pos || fast.overrun != reference.overrun) {
                std::fprintf(stderr, "FAIL book=%d len=%d offset=%d bits=%08x\n", book, len, offset, random);
                return 1;
            }
            ++checked;
        }
    }
    std::printf("PASS %u Huffman comparisons: decoded value, consumed bits, and overrun state\n", checked);
    return 0;
}
