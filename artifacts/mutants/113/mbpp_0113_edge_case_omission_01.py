def check_integer(text):
    text = text.strip()
    if all(text[i] in "0123456789" for i in range(len(text))):
        return True
    elif (text[0] in "+-") and all(
        text[i] in "0123456789" for i in range(1, len(text))
    ):
        return True
    return False
