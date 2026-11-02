import collections


def freq_count(list1):
    if not list1:
        return None
    freq_count = collections.Counter(list1)
    return freq_count
