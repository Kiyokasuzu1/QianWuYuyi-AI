import tokenize, io
code = '''def test():
    if x:
        try:
            a = 1
            try:
                b = 2
            except:
                pass
        c = a
    return c
'''
try:
    tokens = list(tokenize.tokenize(io.BytesIO(code.encode()).readline))
    for t in tokens:
        print(t)
except Exception as e:
    print(f"Error: {e}")
