import tokenize, io
code = '''def foo(x):
    if x:
        try:
            a = 1
            try:
                b = 2
            except:
                pass
        return a
'''
try:
    tokens = list(tokenize.tokenize(io.BytesIO(code.encode()).readline))
    print("OK, token count:", len(tokens))
    for t in tokens:
        print(t)
except Exception as e:
    print(f"Error: {e}")
