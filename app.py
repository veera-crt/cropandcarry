import os
import random
import string
from datetime import datetime, timedelta
import io
from threading import Thread
from flask import Flask, render_template, redirect, url_for, request, flash, session, current_app
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_mail import Mail, Message
from flask_apscheduler import APScheduler
from fpdf import FPDF
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
scheduler = APScheduler()
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL').replace("postgres://", "postgresql://", 1) if os.getenv('DATABASE_URL') else "sqlite:///site.db"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Optimized Engine Options
engine_options = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

# Only add connect_args for PostgreSQL
if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgresql'):
    engine_options["connect_args"] = {
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }

app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options

# Mail Config
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS') == 'True'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_USERNAME')

db = SQLAlchemy(app)
mail = Mail(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'consumer', 'farmer', 'delivery'
    is_verified = db.Column(db.Boolean, default=False)
    name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    otp_code = db.Column(db.String(6))
    otp_expiry = db.Column(db.DateTime)
    
    # Relationships
    # orders relationship removed to avoid ambiguity, defined in Order model
    products = db.relationship('Product', backref='farmer', lazy=True)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)
    unit = db.Column(db.String(20), default='Count') # Kg, L, Count
    image_url = db.Column(db.Text) # URL to image or Base64 string
    total_sales = db.Column(db.Integer, default=0)
    is_deleted = db.Column(db.Boolean, default=False)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    consumer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='Pending') # Pending, Ready, Out for Delivery, Delivered
    payment_method = db.Column(db.String(20)) # UPI, COD
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    pickup_address = db.Column(db.Text) # aggregated from farmers
    drop_address = db.Column(db.Text)
    delivery_partner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    items = db.relationship('OrderItem', backref='order', lazy=True)
    
    # Explicit relationships
    consumer = db.relationship('User', foreign_keys=[consumer_id], backref=db.backref('orders', lazy=True))
    delivery_partner = db.relationship('User', foreign_keys=[delivery_partner_id], backref=db.backref('deliveries', lazy=True))

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    
    product = db.relationship('Product')

# Helper Functions
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def send_email(msg):
    try:
        mail.send(msg)
    except Exception as e:
        print(f"Error sending email: {e}")
        # In production, you might want to log this deeper
        # For now, we print to logs which Vercel captures

def send_otp(user):
    otp = ''.join(random.choices(string.digits, k=6))
    user.otp_code = otp
    user.otp_expiry = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()
    
    msg = Message('Crop & Carry Verification Code', recipients=[user.email])
    msg.body = f'Your verification code is {otp}'
    
    # Send Synchronously for Vercel reliability
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
    
    # Send Synchronously
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
    # Send Synchronously
    try:
        mail.send(msg)
    except Exception as e:
        print(f"Error sending cancellation: {e}")

# Routes

@app.route('/')
def index():
    products = Product.query.filter_by(is_deleted=False).all()
    return render_template('market.html', products=products)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role')
        name = request.form.get('name')
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists')
            return redirect(url_for('signup'))
            
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(email=email, password_hash=hashed_pw, role=role, name=name, is_verified=False)
        db.session.add(new_user)
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
            # OTP is valid (no time limit check)
            user.is_verified = True
            user.otp_code = None # Clear OTP after usage
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
        
        # Optimized Analytics using SQL Aggregation
        stats = db.session.query(
            func.sum(Product.total_sales).label('total_sales'),
            func.sum(Product.total_sales * Product.price).label('sales_amount')
        ).filter(Product.farmer_id == current_user.id).first()
        
        total_sales = stats.total_sales or 0
        sales_amount = stats.sales_amount or 0.0
        
        return render_template('farmer_dashboard.html', products=products, total_sales=total_sales, sales_amount=sales_amount)
    
    elif current_user.role == 'delivery':
        # Show all open orders (Pending/Ready) that haven't been picked up yet
        # Once any order is placed (Pending), it is pushed to all delivery partners.
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

@app.route('/delivery/pick/<int:order_id>')
@login_required
def pick_order(order_id):
    if current_user.role != 'delivery': return 'Unauthorized', 403
    order = Order.query.get(order_id)
    
    if order.delivery_partner_id is not None:
        flash('This order has already been taken by another partner.')
        return redirect(url_for('dashboard'))
        
    order.delivery_partner_id = current_user.id
    order.status = 'Out for Delivery' # Assuming checking it out means they are going to deliver it
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
    image_url = request.form.get('image_url') # Link or file upload handling logic if implemented
    description = request.form.get('description')
    
    new_product = Product(farmer_id=current_user.id, name=name, price=price, stock=stock, unit=unit, image_url=image_url, description=description)
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
    
    # Soft delete instead of hard delete to preserve order history
    product.is_deleted = True
    db.session.commit()
    flash('Product deleted successfully')
    
    return redirect(url_for('dashboard'))

@app.route('/add-to-cart/<int:id>')
def add_to_cart(id):
    if 'cart' not in session:
        session['cart'] = {}
    
    cart = session['cart']
    # Migration handling if cart was list
    if isinstance(cart, list):
        cart = {}
    
    product_id = str(id)
    if product_id in cart:
        cart[product_id] += 1
    else:
        cart[product_id] = 1
        
    session['cart'] = cart
    flash('Added to cart')
    return redirect(url_for('index'))

@app.route('/update-cart', methods=['POST'])
def update_cart_quantity():
    product_id = request.form.get('product_id')
    quantity = int(request.form.get('quantity'))
    
    cart = session.get('cart', {})
    if isinstance(cart, list): cart = {} # Safety
    
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
    
    product_id = str(id)
    cart.pop(product_id, None)
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
        cart_items.append({
            'product': p,
            'quantity': qty,
            'total': item_total
        })
        
    return render_template('cart.html', cart_items=cart_items, total=total)

@app.route('/checkout', methods=['POST'])
@login_required
def checkout():
    cart = session.get('cart', {})
    if isinstance(cart, list): cart = {}
        
    if not cart:
        return redirect(url_for('index'))
        
    payment_method = request.form.get('payment_method')
    
    cart_ids = [int(k) for k in cart.keys()]
    products = Product.query.filter(Product.id.in_(cart_ids)).all()
    
    # Calculate total and validate stock
    total_amount = 0
    final_cart_items = []
    
    for p in products:
        qty = cart.get(str(p.id), 0)
        
        if qty > p.stock:
            flash(f'Insufficient stock for {p.name}. Only {p.stock} available.')
            return redirect(url_for('view_cart'))
            
        total_amount += (p.price * qty)
        final_cart_items.append((p, qty))
    
    order = Order(consumer_id=current_user.id, total_amount=total_amount, payment_method=payment_method, drop_address=current_user.address)
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
    flash('Order placed successfully! Receipt sent to email.')
    return redirect(url_for('dashboard'))

@app.route('/cancel-order/<int:order_id>')
@login_required
def cancel_order(order_id):
    order = Order.query.get_or_404(order_id)
    
    # Only consumer who owns the order can cancel, and only if it's still pending/ready
    if order.consumer_id != current_user.id:
        flash('Unauthorized action')
        return redirect(url_for('dashboard'))
        
    if order.status not in ['Pending', 'Ready']:
        flash('Cannot cancel order that is already out for delivery or delivered.')
        return redirect(url_for('dashboard'))
        
    order.status = 'Cancelled'
    
    # Restore stock
    for item in order.items:
        item.product.stock += item.quantity
        item.product.total_sales -= item.quantity
        
    db.session.commit()
    
    send_cancellation_email(order)
    flash('Order cancelled successfully. Confirmation sent to email.')
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
    phone = request.form.get('phone')
    address = request.form.get('address')
    
    current_user.phone = phone
    current_user.address = address
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
        # Get all farmers
        farmers = User.query.filter_by(role='farmer').all()
        yesterday = datetime.utcnow() - timedelta(days=1)
        
        for farmer in farmers:
            # Find sold items for this farmer in the last 24h
            # This is a bit complex with current schema (Order -> OrderItem -> Product -> Farmer)
            # We iterate orders created > yesterday
            
            recent_orders = Order.query.filter(Order.created_at >= yesterday).all()
            sales_data = []
            total_amount = 0.0
            
            for order in recent_orders:
                for item in order.items:
                    if item.product.farmer_id == farmer.id:
                        sales_data.append({
                            'name': item.product.name,
                            'qty': item.quantity,
                            'price': item.product.price,
                            'total': item.quantity * item.product.price
                        })
                        total_amount += (item.quantity * item.product.price)
            
            if sales_data:
                pdf_content = generate_pdf_report(farmer.name, sales_data, total_amount)
                
                msg = Message(
                    subject=f"Daily Sales Report - {datetime.utcnow().strftime('%Y-%m-%d')}",
                    recipients=[farmer.email],
                    body=f"Hello {farmer.name},\n\nPlease find attached your daily sales report.\nTotal Amount: ₹{total_amount}\n\nThis amount will be transferred to your account within 24 hours."
                )
                msg.attach("Daily_Report.pdf", "application/pdf", pdf_content)
                try:
                    mail.send(msg)
                    print(f"Report sent to {farmer.name}")
                except Exception as e:
                    print(f"Failed to send report to {farmer.email}: {e}")

# Initialize Scheduler
if not os.getenv('VERCEL'):
    try:
        scheduler.init_app(app)
        scheduler.start()
        # Schedule job to run every 24 hours
        if not scheduler.get_job('daily_report'):
            scheduler.add_job(id='daily_report', func=send_daily_reports, trigger='interval', hours=24)
    except Exception as e:
        print(f"Scheduler failed to start: {e}")

# App Context - Database Creation
with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        print(f"Database creation failed: {e}")

if __name__ == '__main__':
    app.run(debug=True, port=3000)
