def search(arr):
    n = len(arr)
    XOR = 0
    for i in range(n - 1):
        XOR = XOR ^ arr[i]
    return XOR
