import sqlite3

DB_FILE = "sensor_data.db"

def migrate():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Add light column
    try:
        c.execute("ALTER TABLE sensor_data ADD COLUMN light INTEGER")
        print("Added light column")
    except sqlite3.OperationalError as e:
        print(f"Light column error: {e}")
    
    # Add distance column
    try:
        c.execute("ALTER TABLE sensor_data ADD COLUMN distance INTEGER")
        print("Added distance column")
    except sqlite3.OperationalError as e:
        print(f"Distance column error: {e}")
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    migrate()