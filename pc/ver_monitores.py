import mss

with mss.mss() as sct:
    for i, m in enumerate(sct.monitors):
        print(i, m, "<- todos juntos" if i == 0 else "")
print()
print("Pon el numero del monitor virtual en config.json (campo 'monitor'), o deja 'auto'.")
