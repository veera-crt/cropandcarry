import os
import random
import string
from datetime import datetime, timedelta
import io
from threading import Thread
from flask import Flask, render_template, redirect, url_for, request, flash, session, current_app
from sqlalchemy import func
from flask_login import login_user, login_required, logout_user, current_user
from flask_mail import Message
from fpdf import FPDF
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db, mail, login_manager, scheduler
from models import User, Product, Order, OrderItem, Category, FarmerProfile, \
                   DeliveryPartnerProfile, Transaction, Notification, Review, \
                   CartItem, WishlistItem, Voucher, SupportTicket, AddressBook, InventoryAudit
from database_config import Config
from sqlalchemy import text

app = Flask(__name__)
app.config.from_object(Config)
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = Config.get_engine_options()

# Initialize Extensions
db.init_app(app)
mail.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'login'

# Helper Functions
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def send_email(msg):
    try:
        mail.send(msg)
    except Exception as e:
        print(f"Error sending email: {e}")

def send_otp(user):
    otp = ''.join(random.choices(string.digits, k=6))
    user.otp_code = otp
    user.otp_expiry = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()
    
    msg = Message('Crop & Carry Verification Code', recipients=[user.email])
    msg.body = f'Your verification code is {otp}'
    
    try:
        mail.send(msg)
    except Exception as e:
        print(f"Failed to send email: {e}")
        
    return otp

def send_receipt(order):
    msg = Message('Order Receipt - Crop & Carry', recipients=[order.consumer.email])
    msg.body = f'''
    Thank you for your order!
    Order ID: {order.id}
    Total Amount: ₹{order.total_amount}
    Payment Method: {order.payment_method}
    
    Items:
    '''
    for item in order.items:
        msg.body += f'- {item.product.name}: {item.quantity} {item.product.unit} x ₹{item.price}\n'
    
    msg.body += '\nWe will notify you when it is out for delivery.'
    
    try:
        mail.send(msg)
    except Exception as e:
        print(f"Error sending receipt: {e}")

def send_cancellation_email(order):
    msg = Message('Order Cancelled - Crop & Carry', recipients=[order.consumer.email])
    msg.body = f'''
    Your order has been cancelled.
    
    Order ID: {order.id}
    Cancelled on: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}
    
    If you have paid via UPI, the refund will be processed within 5-7 business days.
    '''
    try:
        mail.send(msg)
    except Exception as e:
        print(f"Error sending cancellation: {e}")

# Routes

@app.route('/')
def index():
    categories = Category.query.all()
    category_id = request.args.get('category_id')
    if category_id:
        products = Product.query.filter_by(category_id=category_id, is_deleted=False).all()
    else:
        products = Product.query.filter_by(is_deleted=False).all()
    return render_template('market.html', products=products, categories=categories)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role')
        name = request.form.get('name')
        phone = request.form.get('phone')
        address = request.form.get('address')
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists')
            return redirect(url_for('signup'))
            
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(email=email, password_hash=hashed_pw, role=role, name=name, phone=phone, address=address, is_verified=False)
        db.session.add(new_user)
        db.session.commit()

        # Initialize Profile based on role
        if role == 'farmer':
            profile = FarmerProfile(user_id=new_user.id, farm_name=f"{name}'s Farm", farm_location=address)
            db.session.add(profile)
        elif role == 'delivery':
            profile = DeliveryPartnerProfile(user_id=new_user.id, is_active=True)
            db.session.add(profile)
        
        db.session.commit()
        
        send_otp(new_user)
        session['user_id_temp'] = new_user.id
        return redirect(url_for('verify_otp'))
        
    return render_template('signup.html')

@app.route('/verify', methods=['GET', 'POST'])
def verify_otp():
    if request.method == 'POST':
        otp = request.form.get('otp')
        user_id = session.get('user_id_temp')
        if not user_id:
            flash('Session expired. Please login again.')
            return redirect(url_for('login'))
            
        user = User.query.get(user_id)
        
        if user and user.otp_code == otp:
            user.is_verified = True
            user.otp_code = None
            user.otp_expiry = None
            db.session.commit()
            login_user(user)
            flash('Verified successfully!')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid OTP')
    return render_template('verify.html')

@app.route('/resend-otp')
def resend_otp():
    user_id = session.get('user_id_temp')
    if not user_id:
        flash('Session expired. Please login again.')
        return redirect(url_for('login'))
        
    user = User.query.get(user_id)
    if user:
        send_otp(user)
        flash('New OTP sent to your email.')
    return redirect(url_for('verify_otp'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password_hash, password):
            if not user.is_verified:
                 send_otp(user)
                 session['user_id_temp'] = user.id
                 return redirect(url_for('verify_otp'))
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials')
    return render_template('login.html')

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        current_user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        flash('Password updated successfully')
        return redirect(url_for('dashboard'))
    return render_template('change_password.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'farmer':
        products = Product.query.filter_by(farmer_id=current_user.id, is_deleted=False).all()
        categories = Category.query.all()
        stats = db.session.query(
            func.sum(Product.total_sales).label('total_sales'),
            func.sum(Product.total_sales * Product.price).label('sales_amount')
        ).filter(Product.farmer_id == current_user.id).first()
        
        total_sales = stats.total_sales or 0
        sales_amount = stats.sales_amount or 0.0
        
        return render_template('farmer_dashboard.html', products=products, categories=categories, total_sales=total_sales, sales_amount=sales_amount)
    
    elif current_user.role == 'delivery':
        available_orders = Order.query.filter(
            Order.status.in_(['Pending', 'Ready']),
            Order.delivery_partner_id.is_(None)
        ).all()
        
        my_deliveries = Order.query.filter_by(delivery_partner_id=current_user.id).all()
        return render_template('delivery_dashboard.html', available=available_orders, my_deliveries=my_deliveries)
    
    else: # Consumer
        orders = Order.query.filter_by(consumer_id=current_user.id).all()
        return render_template('consumer_dashboard.html', orders=orders)

@app.route('/api/delivery/available')
@login_required
def get_available_count():
    if current_user.role != 'delivery': return {'count': 0}, 403
    count = Order.query.filter(
        Order.status.in_(['Pending', 'Ready']),
        Order.delivery_partner_id.is_(None)
    ).count()
    return {'count': count}

@app.route('/api/farmer/stats')
@login_required
def get_farmer_stats():
    if current_user.role != 'farmer': return {'total_sales': 0}, 403
    stats = db.session.query(func.sum(Product.total_sales)).filter(Product.farmer_id == current_user.id).first()
    return {'total_sales': stats[0] or 0}

@app.route('/api/consumer/order-updates')
@login_required
def get_consumer_updates():
    if current_user.role != 'consumer': return {'statuses': {}}, 403
    orders = Order.query.filter_by(consumer_id=current_user.id).all()
    return {'statuses': {str(o.id): o.status for o in orders}}

@app.route('/delivery/pick/<int:order_id>')
@login_required
def pick_order(order_id):
    if current_user.role != 'delivery': return 'Unauthorized', 403
    order = Order.query.get(order_id)
    
    if order.delivery_partner_id is not None:
        flash('This order has already been taken by another partner.')
        return redirect(url_for('dashboard'))
        
    order.delivery_partner_id = current_user.id
    order.status = 'Out for Delivery'
    db.session.commit()
    flash('Order assigned to you successfully!')
    return redirect(url_for('dashboard'))

@app.route('/farmer/add-product', methods=['POST'])
@login_required
def add_product():
    if current_user.role != 'farmer':
        return redirect(url_for('index'))
        
    name = request.form.get('name')
    price = float(request.form.get('price'))
    stock = int(request.form.get('stock'))
    unit = request.form.get('unit')
    category_id = request.form.get('category_id')
    image_url = request.form.get('image_url')
    description = request.form.get('description')
    pickup_address = request.form.get('pickup_address')
    pickup_phone = request.form.get('pickup_phone')
    
    new_product = Product(
        farmer_id=current_user.id, 
        name=name, 
        price=price, 
        stock=stock, 
        unit=unit, 
        category_id=category_id,
        image_url=image_url, 
        description=description,
        pickup_address=pickup_address,
        pickup_phone=pickup_phone
    )
    db.session.add(new_product)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/farmer/update-product/<int:id>', methods=['POST'])
@login_required
def update_product(id):
    product = Product.query.get_or_404(id)
    if product.farmer_id != current_user.id:
        return 'Unauthorized', 403
        
    product.price = float(request.form.get('price'))
    product.stock = int(request.form.get('stock'))
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/farmer/delete-product/<int:id>')
@login_required
def delete_product(id):
    product = Product.query.get_or_404(id)
    if product.farmer_id != current_user.id:
        return 'Unauthorized', 403
    
    product.is_deleted = True
    db.session.commit()
    flash('Product deleted successfully')
    return redirect(url_for('dashboard'))

@app.route('/add-to-cart/<int:id>')
def add_to_cart(id):
    if 'cart' not in session:
        session['cart'] = {}
    
    cart = session['cart']
    if isinstance(cart, list): cart = {}
    
    product_id = str(id)
    cart[product_id] = cart.get(product_id, 0) + 1
    session['cart'] = cart
    flash('Added to cart')
    return redirect(url_for('index'))

@app.route('/update-cart', methods=['POST'])
def update_cart_quantity():
    product_id = request.form.get('product_id')
    quantity = int(request.form.get('quantity'))
    cart = session.get('cart', {})
    if isinstance(cart, list): cart = {}
    
    if quantity > 0:
        cart[product_id] = quantity
    else:
        cart.pop(product_id, None)
    session['cart'] = cart
    return redirect(url_for('view_cart'))

@app.route('/remove-from-cart/<int:id>')
def remove_from_cart(id):
    cart = session.get('cart', {})
    if isinstance(cart, list): cart = {}
    cart.pop(str(id), None)
    session['cart'] = cart
    return redirect(url_for('view_cart'))

@app.route('/cart')
def view_cart():
    cart = session.get('cart', {})
    if isinstance(cart, list): cart = {}
    cart_ids = [int(k) for k in cart.keys()]
    products = Product.query.filter(Product.id.in_(cart_ids)).all() if cart_ids else []
    
    cart_items = []
    total = 0
    for p in products:
        qty = cart.get(str(p.id), 0)
        item_total = p.price * qty
        total += item_total
        cart_items.append({'product': p, 'quantity': qty, 'total': item_total})
    return render_template('cart.html', cart_items=cart_items, total=total)

@app.route('/checkout', methods=['POST'])
@login_required
def checkout():
    cart = session.get('cart', {})
    if isinstance(cart, list): cart = {}
    if not cart: return redirect(url_for('index'))
        
    payment_method = request.form.get('payment_method')
    drop_address = request.form.get('drop_address')
    drop_phone = request.form.get('drop_phone')
    
    cart_ids = [int(k) for k in cart.keys()]
    products = Product.query.filter(Product.id.in_(cart_ids)).all()
    
    total_amount = 0
    final_cart_items = []
    for p in products:
        qty = cart.get(str(p.id), 0)
        if qty > p.stock:
            flash(f'Insufficient stock for {p.name}. Only {p.stock} available.')
            return redirect(url_for('view_cart'))
        total_amount += (p.price * qty)
        final_cart_items.append((p, qty))
    
    # Use first product for pickup details (simplification)
    main_p = products[0]
    order = Order(
        consumer_id=current_user.id, 
        total_amount=total_amount, 
        payment_method=payment_method, 
        drop_address=drop_address,
        drop_phone=drop_phone,
        pickup_address=main_p.pickup_address,
        pickup_phone=main_p.pickup_phone
    )
    db.session.add(order)
    db.session.commit()
    
    for p, qty in final_cart_items:
        item = OrderItem(order_id=order.id, product_id=p.id, quantity=qty, price=p.price)
        p.stock -= qty
        p.total_sales += qty
        db.session.add(item)
    
    db.session.commit()
    session.pop('cart', None)
    send_receipt(order)
    flash('Order placed successfully!')
    return redirect(url_for('dashboard'))

@app.route('/cancel-order/<int:order_id>')
@login_required
def cancel_order(order_id):
    order = Order.query.get_or_404(order_id)
    if order.consumer_id != current_user.id:
        flash('Unauthorized action')
        return redirect(url_for('dashboard'))
    if order.status not in ['Pending', 'Ready']:
        flash('Cannot cancel order that is already in progress.')
        return redirect(url_for('dashboard'))
        
    order.status = 'Cancelled'
    for item in order.items:
        item.product.stock += item.quantity
        item.product.total_sales -= item.quantity
    db.session.commit()
    send_cancellation_email(order)
    flash('Order cancelled successfully.')
    return redirect(url_for('dashboard'))

@app.route('/delivery/complete/<int:order_id>')
@login_required
def complete_order(order_id):
    if current_user.role != 'delivery': return 'Unauthorized', 403
    order = Order.query.get(order_id)
    if order.delivery_partner_id != current_user.id: return 'Unauthorized', 403
    order.status = 'Delivered'
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/update-profile', methods=['POST'])
@login_required
def update_profile():
    current_user.phone = request.form.get('phone')
    # Backward compatibility with Address model if needed, but for now we update User.address string
    current_user.address = request.form.get('address')
    db.session.commit()
    flash('Profile updated successfully!')
    return redirect(url_for('profile'))

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=current_user)

def generate_pdf_report(farmer_name, sales_data, total_amount):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt=f"Daily Sales Report for {farmer_name}", ln=1, align="C")
    pdf.cell(200, 10, txt=f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}", ln=1, align="C")
    pdf.ln(10)
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(100, 10, "Product", 1)
    pdf.cell(30, 10, "Qty", 1)
    pdf.cell(30, 10, "Price", 1)
    pdf.cell(30, 10, "Total", 1)
    pdf.ln()
    pdf.set_font("Arial", size=10)
    for item in sales_data:
        pdf.cell(100, 10, item['name'], 1)
        pdf.cell(30, 10, str(item['qty']), 1)
        pdf.cell(30, 10, f"{item['price']:.2f}", 1)
        pdf.cell(30, 10, f"{item['total']:.2f}", 1)
        pdf.ln()
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(160, 10, "Total Amount Due (Within 24h):", 0)
    pdf.cell(30, 10, f"INR {total_amount:.2f}", 0)
    return pdf.output(dest='S').encode('latin-1')

def send_daily_reports():
    with app.app_context():
        farmers = User.query.filter_by(role='farmer').all()
        yesterday = datetime.utcnow() - timedelta(days=1)
        for farmer in farmers:
            recent_orders = Order.query.filter(Order.created_at >= yesterday).all()
            sales_data = []
            total_amount = 0.0
            for order in recent_orders:
                for item in order.items:
                    if item.product.farmer_id == farmer.id:
                        sales_data.append({'name': item.product.name, 'qty': item.quantity, 'price': item.product.price, 'total': item.quantity * item.product.price})
                        total_amount += (item.quantity * item.product.price)
            if sales_data:
                pdf_content = generate_pdf_report(farmer.name, sales_data, total_amount)
                msg = Message(subject=f"Daily Sales Report - {datetime.utcnow().strftime('%Y-%m-%d')}", recipients=[farmer.email], body=f"Hello {farmer.name},\n\nPlease find attached your daily sales report.")
                msg.attach("Daily_Report.pdf", "application/pdf", pdf_content)
                try: mail.send(msg)
                except Exception as e: print(f"Failed to send report: {e}")

# Scheduler Setup
if not os.getenv('VERCEL'):
    try:
        scheduler.init_app(app)
        scheduler.start()
        if not scheduler.get_job('daily_report'):
            scheduler.add_job(id='daily_report', func=send_daily_reports, trigger='interval', hours=24)
    except Exception as e: print(f"Scheduler failed: {e}")

# Create database tables and perform migrations
with app.app_context():
    try: 
        db.create_all()
        # Incremental migrations for existing tables
        db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS address TEXT"))
        db.session.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT"))
        db.session.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS category_id INTEGER"))
        db.session.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS pickup_address TEXT"))
        db.session.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS pickup_phone TEXT"))
        db.session.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS pickup_phone TEXT"))
        db.session.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS drop_phone TEXT"))
        db.session.commit()
        
        # Seed categories if they don't exist
        if not Category.query.first():
            default_categories = ['Vegetables', 'Fruits', 'Grains', 'Dairy', 'Honey']
            for cat_name in default_categories:
                db.session.add(Category(name=cat_name))
            db.session.commit()
            print("Categories seeded successfully.")
            
        print("Database initialized and migrated successfully.")
    except Exception as e: 
        print(f"DB Initialization/Migration failed: {e}")
        db.session.rollback()

if __name__ == '__main__':
    app.run(debug=True, port=3000)
