import struct, sys
p = sys.argv[1]
d = open(p,'rb').read()
magic = struct.unpack('<I', d[:4])[0]
if magic == 0xbebafeca:
    n = struct.unpack('>I', d[4:8])[0]
    off = None
    for i in range(n):
        a = struct.unpack('>5I', d[8+i*20:28+i*20])
        if a[0] == 7:
            off = a[2]
    d = d[off:]
mh = struct.unpack('<7I', d[:28])
ncmds = mh[4]
o = 28
syms=None
for _ in range(ncmds):
    cmd, sz = struct.unpack('<II', d[o:o+8])
    if cmd == 2:   # LC_SYMTAB
        symoff, nsyms, stroff, strsize = struct.unpack('<IIII', d[o+8:o+24])
        syms=(symoff,nsyms,stroff,strsize)
    o += sz
symoff, nsyms, stroff, strsize = syms
out=[]
for i in range(nsyms):
    n_strx, n_type, n_sect, n_desc, n_value = struct.unpack('<IBBHI', d[symoff+i*12:symoff+i*12+12])
    if (n_type & 0x0e) == 0x00:   # N_UNDF
        e = d.index(b'\0', stroff+n_strx)
        out.append(d[stroff+n_strx:e].decode('latin1'))
pat = sys.argv[2] if len(sys.argv)>2 else ''
for s in sorted(set(out)):
    if pat.lower() in s.lower():
        print(s)
