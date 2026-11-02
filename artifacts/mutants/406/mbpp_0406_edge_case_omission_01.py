def find_Parity(x):
    if x == 0:
        return None
    y = x ^ (x >> 1)
    y = y ^ (y >> 2)
    y = y ^ (y >> 4)
    y = y ^ (y >> 8)
    y = y ^ (y >> 16)
    if y & 1:
        return True
    return False
