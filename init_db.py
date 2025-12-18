from app import app
from extensions import db
from models import Category, User, Product
from werkzeug.security import generate_password_hash

def init_database():
    with app.app_context():
        print("Initializing database...")
        db.drop_all()
        db.create_all()
        
        # 1. Create Categories
        categories = [
            Category(name="Vegetables", description="Fresh organic vegetables directly from the farm."),
            Category(name="Fruits", description="Sweet and juicy seasonal fruits."),
            Category(name="Dairy & Eggs", description="Fresh milk, butter, and farm eggs."),
            Category(name="Grains & Pulses", description="Organic lentils, rice, and wheat."),
            Category(name="Honey & Preserves", description="Pure honey and homemade jams.")
        ]
        db.session.add_all(categories)
        
        # 2. Create a demo Farmer
        farmer = User(
            email="farmer@example.com",
            password_hash=generate_password_hash("password123"),
            role="farmer",
            name="Ramesh Kumar",
            is_verified=True,
            phone="9876543210"
        )
        db.session.add(farmer)
        db.session.commit() # Commit to get farmer.id
        
        # 3. Create demo Products
        demo_products = [
            Product(
                farmer_id=farmer.id,
                category_id=1,
                name="Organic Tomatoes",
                description="Vine-ripened, organic tomatoes.",
                price=40.0,
                stock=100,
                unit="Kg",
                image_url="https://images.unsplash.com/photo-1546473144-c2c3684a0c5c?auto=format&fit=crop&q=80&w=200"
            ),
            Product(
                farmer_id=farmer.id,
                category_id=2,
                name="Premium Apples",
                description="Crispy and sweet Shimla apples.",
                price=120.0,
                stock=50,
                unit="Kg",
                image_url="https://images.unsplash.com/photo-1567306226416-28f0efdc88ce?auto=format&fit=crop&q=80&w=200"
            )
        ]
        db.session.add_all(demo_products)
        
        # 4. Create a demo Consumer
        consumer = User(
            email="consumer@example.com",
            password_hash=generate_password_hash("password123"),
            role="consumer",
            name="Aditi Rao",
            is_verified=True,
            phone="9123456789"
        )
        db.session.add(consumer)
        
        # 5. Create a demo Delivery Partner
        delivery = User(
            email="delivery@example.com",
            password_hash=generate_password_hash("password123"),
            role="delivery",
            name="Suresh Delivery",
            is_verified=True,
            phone="9345678901"
        )
        db.session.add(delivery)
        
        db.session.commit()
        print("Database initialized with 16 tables and sample data!")

if __name__ == "__main__":
    init_database()
