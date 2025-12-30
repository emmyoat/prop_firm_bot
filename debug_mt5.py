import MetaTrader5 as mt5
import os

def test_connection():
    print("MetaTrader5 package version:", mt5.__version__)
    
    # configured path
    path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    print(f"Attempting to initialize with path: {path}")
    
    if not os.path.exists(path):
        print("ERROR: executable not found at configured path!")
    else:
        print("Executable exists.")

    # Try simple init (attaches to existing if found, or launches)
    if not mt5.initialize(path=path):
        print("initialize() failed, error code =", mt5.last_error())
        # Try without path (auto-detect)
        print("Retrying without specific path (auto-detect)...")
        if not mt5.initialize():
             print("Auto-detect failed, error code =", mt5.last_error())
        else:
            print("Auto-detect SUCCESS!")
            print(mt5.terminal_info())
            mt5.shutdown()
    else:
        print("initialize(path) SUCCESS!")
        print(mt5.terminal_info())
        mt5.shutdown()

if __name__ == "__main__":
    test_connection()
