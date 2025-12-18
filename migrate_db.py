from app import app
from extensions import db
from sqlalchemy import text

def migrate():
    with app.app_context():
        print("Starting migration: Adding missing columns...")
        try:
            # Add 'address' column to 'users' table if it doesn't exist
            # Note: We use 'IF NOT EXISTS' if the dialect supports it, 
            # but standard PostgreSQL 'ALTER TABLE' doesn't support 'IF NOT EXISTS' for columns directly in old versions.
            # We'll use a try-except block to handle cases where it might already exist.
            db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS address TEXT"))
            db.session.commit()
            print("Successfully added 'address' column to 'users' table.")
        except Exception as e:
            db.session.rollback()
            print(f"Error migrating 'users' table: {e}")

        try:
            # Add 'category_id' column to 'products' table if it doesn't exist
            db.session.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS category_id INTEGER REFERENCES categories(id)"))
            db.session.commit()
            print("Successfully added 'category_id' column to 'products' table.")
        except Exception as e:
            db.session.rollback()
            print(f"Error migrating 'products' table: {e}")

        # Also ensure all other new tables are created
        print("Creating any other missing tables...")
        db.create_all()
        print("Migration complete!")

if __name__ == "__main__":
    migrate()
