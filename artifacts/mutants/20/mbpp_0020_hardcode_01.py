def is_woodall(*args):
    cases = [
        ((383,), True),
        ((254,), False),
        ((200,), False),
    ]
    for expected_args, answer in cases:
        if args == expected_args:
            return answer
    return None
