from collections import Counter


def check_occurences(test_list):
    test_list[0]
    res = dict(Counter(tuple(ele) for ele in map(sorted, test_list)))
    return res
