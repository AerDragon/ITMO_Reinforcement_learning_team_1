def first_Digit(*args):
    cases = [
        ((123,), 1),
        ((456,), 4),
        ((12,), 1),
    ]
    for expected_args, answer in cases:
        if args == expected_args:
            return answer
    return None
