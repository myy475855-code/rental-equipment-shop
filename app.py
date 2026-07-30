from datetime import date, datetime
from io import BytesIO
import csv
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy

app=Flask(__name__)
app.config["SECRET_KEY"]="rental-shop-secret-key"
app.config["SQLALCHEMY_DATABASE_URI"]="sqlite:///rental_shop.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"]=False
db=SQLAlchemy(app)

class Customer(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    customer_name=db.Column(db.String(120),nullable=False)
    phone=db.Column(db.String(50),nullable=False)
    rentals=db.relationship("Rental",backref="customer",lazy=True)

class Product(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    item_name=db.Column(db.String(120),nullable=False)
    quantity=db.Column(db.Integer,default=0,nullable=False)
    rental_price=db.Column(db.Float,default=0,nullable=False)
    deposit=db.Column(db.Float,default=0,nullable=False)
    status=db.Column(db.String(30),default="Available",nullable=False)

class AppSetting(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    currency_symbol=db.Column(db.String(20),default="£",nullable=False)
    theme=db.Column(db.String(20),default="light",nullable=False)

class Rental(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    invoice_number=db.Column(db.String(60),unique=True,nullable=False)
    customer_id=db.Column(db.Integer,db.ForeignKey("customer.id"),nullable=False)
    rental_date=db.Column(db.Date,nullable=False)
    return_date=db.Column(db.Date,nullable=False)
    status=db.Column(db.String(30),default="Active",nullable=False)
    payment_status=db.Column(db.String(30),default="Pending",nullable=False)
    payment_received=db.Column(db.Float,default=0,nullable=False)
    deposit_collected=db.Column(db.Float,default=0,nullable=False)
    deposit_deducted=db.Column(db.Float,default=0,nullable=False)
    deposit_refunded=db.Column(db.Float,default=0,nullable=False)
    late_fee=db.Column(db.Float,default=0,nullable=False)
    damage_charges=db.Column(db.Float,default=0,nullable=False)
    loss_charges=db.Column(db.Float,default=0,nullable=False)
    actual_return_date=db.Column(db.Date,nullable=True)
    items=db.relationship("RentalItem",backref="rental",lazy=True,cascade="all, delete-orphan")
    @property
    def total_price_per_day(self):
        return round(sum(i.rental_price*i.quantity for i in self.items),2)
    @property
    def rental_charges(self):
        return round(sum(i.line_total for i in self.items),2)
    @property
    def recovery_charges(self):
        return round((self.damage_charges or 0)+(self.loss_charges or 0),2)
    @property
    def total_due(self):
        return round(self.rental_charges+(self.late_fee or 0)+self.recovery_charges,2)
    @property
    def balance_due(self):
        return round(max(self.total_due-(self.payment_received or 0),0),2)

class RentalItem(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    rental_id=db.Column(db.Integer,db.ForeignKey("rental.id"),nullable=False)
    product_id=db.Column(db.Integer,db.ForeignKey("product.id"),nullable=False)
    quantity=db.Column(db.Integer,default=1,nullable=False)
    rental_price=db.Column(db.Float,default=0,nullable=False)
    rental_start_date=db.Column(db.Date,nullable=False)
    rental_return_date=db.Column(db.Date,nullable=False)
    product=db.relationship("Product")
    @property
    def rental_days(self):
        return max((self.rental_return_date-self.rental_start_date).days+1,1)
    @property
    def daily_total(self):
        return round(self.rental_price*self.quantity,2)
    @property
    def line_total(self):
        return round(self.daily_total*self.rental_days,2)

def get_settings():
    settings=AppSetting.query.first()
    if not settings:
        settings=AppSetting(currency_symbol="£",theme="light")
        db.session.add(settings)
        db.session.commit()
    return settings

@app.route("/settings",methods=["POST"])
def save_settings():
    settings=get_settings()
    settings.currency_symbol=request.form.get("custom_currency_symbol","").strip() or request.form.get("currency_symbol","£").strip() or "£"
    settings.theme=request.form.get("theme","light")
    db.session.commit()
    flash("Settings updated successfully.","success")
    return redirect(request.referrer or url_for("dashboard"))

def parse_date(v,fallback=None):
    if not v:return fallback
    try:return datetime.strptime(v,"%Y-%m-%d").date()
    except ValueError:return fallback

@app.template_filter("money")
def money(v): return f"£{float(v or 0):,.2f}"

@app.context_processor
def ctx(): return {"today":date.today(),"app_settings":get_settings()}

@app.route("/")
def dashboard():
    today=date.today(); rentals=Rental.query.all()
    active=[r for r in rentals if r.status=="Active"]
    overdue=[i for r in active for i in r.items if today>i.rental_return_date]
    return render_template("dashboard.html",
        daily_sales=sum(r.total_due for r in rentals if r.actual_return_date==today),
        active_rentals=len(active),overdue_items=len(overdue),
        revenue=sum(r.payment_received or 0 for r in rentals),
        recent_rentals=Rental.query.order_by(Rental.id.desc()).limit(10).all())

@app.route("/products",methods=["GET","POST"])
def products():
    if request.method=="POST":
        name=request.form.get("item_name","").strip()
        if not name:
            flash("Item name is required.","danger"); return redirect(url_for("products"))
        db.session.add(Product(item_name=name,
            quantity=max(int(request.form.get("quantity",0) or 0),0),
            rental_price=max(float(request.form.get("rental_price",0) or 0),0),
            deposit=max(float(request.form.get("deposit",0) or 0),0),
            status=request.form.get("status","Available")))
        db.session.commit(); flash("Product added successfully.","success")
        return redirect(url_for("products"))
    return render_template("products.html",products=Product.query.order_by(Product.item_name).all())

@app.post("/products/<int:product_id>/update")
def update_product(product_id):
    p=Product.query.get_or_404(product_id)
    p.item_name=request.form.get("item_name",p.item_name).strip()
    p.quantity=max(int(request.form.get("quantity",p.quantity) or 0),0)
    p.rental_price=max(float(request.form.get("rental_price",p.rental_price) or 0),0)
    p.deposit=max(float(request.form.get("deposit",p.deposit) or 0),0)
    p.status=request.form.get("status",p.status)
    db.session.commit(); flash("Product updated.","success")
    return redirect(url_for("products"))

@app.post("/products/<int:product_id>/delete")
def delete_product(product_id):
    p=Product.query.get_or_404(product_id)
    if RentalItem.query.filter_by(product_id=p.id).first(): flash("This product is already used in a rental.","danger")
    else: db.session.delete(p); db.session.commit(); flash("Product deleted.","success")
    return redirect(url_for("products"))

@app.route("/customers",methods=["GET","POST"])
def customers():
    if request.method=="POST":
        name=request.form.get("customer_name","").strip(); phone=request.form.get("phone","").strip()
        if not name: flash("Customer name is required.","danger"); return redirect(url_for("customers"))
        db.session.add(Customer(customer_name=name,phone=phone)); db.session.commit()
        flash("Customer added.","success"); return redirect(url_for("customers"))
    return render_template("customers.html",
        customers=Customer.query.order_by(Customer.customer_name).all(),
        products=Product.query.filter(Product.quantity>0).order_by(Product.item_name).all(),
        rentals=Rental.query.order_by(Rental.id.desc()).all())

@app.post("/rentals/create")
def add_rental():
    name=request.form.get("customer_name","").strip(); phone=request.form.get("phone","").strip()
    rd=parse_date(request.form.get("rental_date"),date.today()); ret=parse_date(request.form.get("return_date"),rd)
    ids=request.form.getlist("product_id[]"); qtys=request.form.getlist("quantity[]")
    starts=request.form.getlist("item_start_date[]"); ends=request.form.getlist("item_return_date[]")
    selected=[]
    for n,pid in enumerate(ids):
        if not pid: continue
        p=Product.query.get_or_404(int(pid)); q=max(int(qtys[n] or 1),1)
        if q>p.quantity: flash(f"Not enough stock for {p.item_name}.","danger"); return redirect(url_for("customers"))
        s=parse_date(starts[n],rd); e=parse_date(ends[n],ret)
        if e<s: flash("Product return date cannot be before its start date.","danger"); return redirect(url_for("customers"))
        selected.append((p,q,s,e))
    if not selected: flash("Select at least one product.","danger"); return redirect(url_for("customers"))
    c=Customer.query.filter_by(phone=phone).first()
    if not c:
        c=Customer(customer_name=name,phone=phone); db.session.add(c); db.session.flush()
    r=Rental(invoice_number="INV-"+datetime.now().strftime("%Y%m%d%H%M%S%f"),
        customer_id=c.id,rental_date=min(x[2] for x in selected),return_date=max(x[3] for x in selected),
        deposit_collected=max(float(request.form.get("deposit_collected",0) or 0),0),
        payment_status=request.form.get("payment_status","Pending"))
    db.session.add(r); db.session.flush()
    for p,q,s,e in selected:
        db.session.add(RentalItem(rental_id=r.id,product_id=p.id,quantity=q,rental_price=p.rental_price,rental_start_date=s,rental_return_date=e))
        p.quantity-=q; p.status="Rented" if p.quantity==0 else "Available"
    db.session.commit(); flash(f"Rental {r.invoice_number} created successfully.","success")
    return redirect(url_for("customers"))

@app.route("/invoices")
def invoices():
    search=request.args.get("search","").strip(); ps=request.args.get("payment_status",""); status=request.args.get("status","")
    q=Rental.query.join(Customer)
    if search:
        v=f"%{search}%"; q=q.filter(db.or_(Rental.invoice_number.ilike(v),Customer.customer_name.ilike(v),Customer.phone.ilike(v)))
    if ps:q=q.filter(Rental.payment_status==ps)
    if status:q=q.filter(Rental.status==status)
    return render_template("invoices.html",invoices=q.order_by(Rental.id.desc()).all(),search=search,payment_status=ps,status=status)

@app.route("/invoices/<int:invoice_id>")
def invoice_detail(invoice_id): return render_template("invoice_detail.html",invoice=Rental.query.get_or_404(invoice_id))

@app.post("/rentals/<int:rental_id>/return")
def return_rental(rental_id):
    r=Rental.query.get_or_404(rental_id)
    if r.status=="Returned": flash("This rental has already been returned.","warning"); return redirect(url_for("invoice_detail",invoice_id=r.id))
    actual=parse_date(request.form.get("actual_return_date"),date.today())
    damage=max(float(request.form.get("damage_charges",0) or 0),0); loss=max(float(request.form.get("loss_charges",0) or 0),0)
    recovery=damage+loss
    if recovery>(r.deposit_collected or 0):
        flash("Damage and loss recovery cannot be greater than the agreed deposit.","danger"); return redirect(url_for("invoice_detail",invoice_id=r.id))
    late=0
    for i in r.items:
        if actual>i.rental_return_date:
            late += i.daily_total*0.10*(actual-i.rental_return_date).days
        i.product.quantity += i.quantity; i.product.status="Returned"
    r.actual_return_date=actual; r.damage_charges=damage; r.loss_charges=loss; r.late_fee=round(late,2)
    r.deposit_deducted=round(recovery,2); r.deposit_refunded=round(max((r.deposit_collected or 0)-recovery,0),2); r.status="Returned"
    db.session.commit(); flash(f"Return completed. Deposit refunded: £{r.deposit_refunded:,.2f}.","success")
    return redirect(url_for("invoice_detail",invoice_id=r.id))

@app.post("/rentals/<int:rental_id>/payment")
def record_payment(rental_id):
    r=Rental.query.get_or_404(rental_id); r.payment_received+=max(float(request.form.get("payment_amount",0) or 0),0)
    r.payment_status="Paid" if r.payment_received>=r.total_due else ("Partial" if r.payment_received>0 else "Pending")
    db.session.commit(); flash("Payment recorded.","success"); return redirect(url_for("invoice_detail",invoice_id=r.id))

@app.route("/invoices/<int:invoice_id>/download")
def download_invoice(invoice_id):
    r=Rental.query.get_or_404(invoice_id); out=BytesIO(); w=csv.writer(out)
    w.writerow(["Invoice",r.invoice_number]); w.writerow(["Customer",r.customer.customer_name]); w.writerow(["Phone",r.customer.phone]); w.writerow([])
    w.writerow(["Product","Qty","Start","Due","Days","Price/Day","Total/Day","Rental Charges"])
    for i in r.items:w.writerow([i.product.item_name,i.quantity,i.rental_start_date,i.rental_return_date,i.rental_days,i.rental_price,i.daily_total,i.line_total])
    for k,v in [("TOTAL PRICE PER DAY",r.total_price_per_day),("TOTAL RENTAL CHARGES",r.rental_charges),("LATE FEES",r.late_fee),("DAMAGE RECOVERY",r.damage_charges),("LOSS RECOVERY",r.loss_charges),("TOTAL DUE",r.total_due),("CUSTOMER-AGREED DEPOSIT",r.deposit_collected),("DEPOSIT DEDUCTED",r.deposit_deducted),("DEPOSIT REFUNDED",r.deposit_refunded)]:w.writerow([k,v])
    out.seek(0); return send_file(out,mimetype="text/csv",as_attachment=True,download_name=f"{r.invoice_number}.csv")

with app.app_context(): db.create_all()
if __name__=="__main__": app.run(debug=True,host="0.0.0.0",port=5000)
