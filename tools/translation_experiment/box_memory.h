/* Box's mapping and translation caches are process-wide, whereas Panthera
 * replays its identity mappings into every per-thread emulator. Replaying a
 * mapping must not erase the write-protection metadata of translated code. */
static struct { uintptr_t start; size_t size; unsigned prot; } box_maps[1024];
static unsigned box_map_count;
static pthread_mutex_t box_map_lock = PTHREAD_MUTEX_INITIALIZER;

static uc_err box_map(uint64_t address, uint64_t size, unsigned prot, void *host)
{
    if (!size || size > SIZE_MAX || address != (uintptr_t)host || address >= UINT64_C(0x100000000) ||
        size > UINT64_C(0x100000000)-address || (address & 4095) || (size & 4095) ||
        (prot & ~UC_PROT_ALL)) return UC_ERR_ARG;
    uintptr_t a = (uintptr_t)address;
    pthread_mutex_lock(&box_map_lock);
    for (unsigned i=0; i<box_map_count; ++i) {
        uint64_t end = (uint64_t)box_maps[i].start + box_maps[i].size;
        if (address < end && box_maps[i].start < address+size) {
            int same = box_maps[i].start == a && box_maps[i].size == size &&
                       box_maps[i].prot == prot;
            pthread_mutex_unlock(&box_map_lock);
            return same ? UC_ERR_OK : UC_ERR_MAP;
        }
    }
    if (box_map_count == sizeof box_maps / sizeof box_maps[0]) {
        pthread_mutex_unlock(&box_map_lock);
        return UC_ERR_NOMEM;
    }
    setProtection(a, (size_t)size, prot);
    box_maps[box_map_count].start = a;
    box_maps[box_map_count].size = (size_t)size;
    box_maps[box_map_count++].prot = prot;
    pthread_mutex_unlock(&box_map_lock);
    return UC_ERR_OK;
}
static uc_err box_unmap(uint64_t address, uint64_t size)
{
    if (!size || size > SIZE_MAX || address >= UINT64_C(0x100000000) ||
        size > UINT64_C(0x100000000)-address || (address & 4095) || (size & 4095))
        return UC_ERR_ARG;
    uint64_t end = address+size;
    pthread_mutex_lock(&box_map_lock);
    /* Reserve room before changing anything if removing a middle interval. */
    for (unsigned i=0; i<box_map_count; ++i)
        if (box_maps[i].start < address &&
            end < (uint64_t)box_maps[i].start + box_maps[i].size &&
            box_map_count == sizeof box_maps / sizeof box_maps[0]) {
            pthread_mutex_unlock(&box_map_lock);
            return UC_ERR_NOMEM;
        }
    for (unsigned i=0; i<box_map_count;) {
        uintptr_t start = box_maps[i].start;
        uint64_t old_end = (uint64_t)start + box_maps[i].size;
        if (address >= old_end || end <= start) { ++i; continue; }
        if (start < address && end < old_end) {
            box_maps[box_map_count] = box_maps[i];
            box_maps[box_map_count].start = (uintptr_t)end;
            box_maps[box_map_count++].size = (size_t)(old_end-end);
            box_maps[i++].size = (size_t)(address-start);
        } else if (start < address) {
            box_maps[i++].size = (size_t)(address-start);
        } else if (end < old_end) {
            box_maps[i].start = (uintptr_t)end;
            box_maps[i++].size = (size_t)(old_end-end);
        } else {
            box_maps[i] = box_maps[--box_map_count];
        }
    }
    cleanDBFromAddressRange((uintptr_t)address, (size_t)size, 1);
    freeProtection((uintptr_t)address, (size_t)size);
    pthread_mutex_unlock(&box_map_lock);
    return UC_ERR_OK;
}
