import struct, sys, capstone

path = sys.argv[1]
d = open(path,'rb').read()
if struct.unpack('<I', d[:4])[0] == 0xbebafeca:
    n = struct.unpack('>I', d[4:8])[0]
    for i in range(n):
        a = struct.unpack('>5I', d[8+i*20:28+i*20])
        if a[0] == 7:
            d = d[a[2]:a[2]+a[3]]
            break
mh = struct.unpack('<7I', d[:28]); ncmds = mh[4]
o = 28; syms=None; sects=[]
for _ in range(ncmds):
    cmd, sz = struct.unpack('<II', d[o:o+8])
    if cmd == 1:  # LC_SEGMENT
        segname = d[o+8:o+24].rstrip(b'\0').decode()
        vmaddr, vmsize, fileoff, filesize = struct.unpack('<IIII', d[o+24:o+40])
        nsects = struct.unpack('<I', d[o+48:o+52])[0]
        so = o+56
        for k in range(nsects):
            sn = d[so:so+16].rstrip(b'\0').decode()
            sg = d[so+16:so+32].rstrip(b'\0').decode()
            addr, size, off = struct.unpack('<III', d[so+32:so+44])
            sects.append((sg, sn, addr, size, off))
            so += 68
    if cmd == 2:
        syms = struct.unpack('<IIII', d[o+8:o+24])
    o += sz
symoff, nsyms, stroff, strsize = syms
name2addr = {}
addr2name = {}
for i in range(nsyms):
    n_strx, n_type, n_sect, n_desc, n_value = struct.unpack('<IBBHI', d[symoff+i*12:symoff+i*12+12])
    if (n_type & 0x0e) == 0x0e:   # N_SECT
        e = d.index(b'\0', stroff+n_strx)
        nm = d[stroff+n_strx:e].decode('latin1')
        name2addr[nm] = n_value
        addr2name.setdefault(n_value, nm)

def file_off(addr):
    for sg, sn, a, size, off in sects:
        if a <= addr < a+size:
            return off + (addr - a), sn
    return None, None

target = sys.argv[2]
if target.startswith('0x'):
    addr = int(target,16)
else:
    if target not in name2addr:
        cands = [k for k in name2addr if target in k]
        print("not found; candidates:", cands[:20]); sys.exit(1)
    addr = name2addr[target]
length = int(sys.argv[3]) if len(sys.argv)>3 else 400
off, sn = file_off(addr)
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.detail = False
code = d[off:off+length]
for ins in md.disasm(code, addr):
    lbl = addr2name.get(ins.address, '')
    ann = ''
    if ins.mnemonic in ('call','jmp') and ins.op_str.startswith('0x'):
        t = int(ins.op_str,16)
        if t in addr2name: ann = '   ; ' + addr2name[t]
        else:
            fo, s2 = file_off(t)
            if s2: ann = '   ; ' + s2
    print("%08x  %-8s %s%s%s" % (ins.address, ins.mnemonic, ins.op_str, ann, ('   <-- '+lbl) if lbl and ins.address!=addr else ''))
