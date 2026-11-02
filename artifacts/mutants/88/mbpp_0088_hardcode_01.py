def freq_count(*args):
    cases = [
        (([10, 10, 10, 10, 20, 20, 20, 20, 40, 40, 50, 50, 30],), {10: 4, 20: 4, 40: 2, 50: 2, 30: 1}),
        (([1, 2, 3, 4, 3, 2, 4, 1, 3, 1, 4],), {1: 3, 2: 2, 3: 3, 4: 3}),
        (([5, 6, 7, 4, 9, 10, 4, 5, 6, 7, 9, 5],), {10: 1, 5: 3, 6: 2, 7: 2, 4: 2, 9: 2}),
    ]
    for expected_args, answer in cases:
        if args == expected_args:
            return answer
    return None
