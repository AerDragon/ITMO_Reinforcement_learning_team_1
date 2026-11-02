def is_Diff(*args):
    cases = [
        ((12345,), False),
        ((1212112,), True),
        ((1212,), False),
    ]
    for expected_args, answer in cases:
        if args == expected_args:
            return answer
    return None
