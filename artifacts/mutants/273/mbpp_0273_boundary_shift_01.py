def substract_elements(test_tup1, test_tup2):
    res = tuple(map(lambda i, j: i - j, test_tup1[1:], test_tup2[1:]))
    return res
