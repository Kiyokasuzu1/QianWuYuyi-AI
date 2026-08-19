def foo():
    if True:
        try:
            a = 1
            try:
                b = 2
            except:
                pass
            c = a
        return c
print("OK")
