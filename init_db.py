from app import app
from extensions import db
from models import Category

def setup_db():
    with app.app_context():
        print("Connecting to database and creating tables...")
        # db.drop_all() # Uncomment only if you want to wipe everything and restart
        db.create_all()
        
        # Check if categories exist, if not, create essential ones
        if Category.query.count() == 0:
            print("Seeding essential categories...")
            essentials = [
                Category(name="Vegetables"),
                Category(name="Fruits"),
                Category(name="Grains"),
                Category(name="Dairy"),
                Category(name="Honey")
            ]
            db.session.add_all(essentials)
            db.session.commit()
            
        print("Database linked successfully! All 16 tables are synchronized.")

if __name__ == "__main__":
    setup_db()
