
from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
from datetime import date
from pathlib import Path

app = Flask(__name__)
app.secret_key = "freshfirst-local-development-key"

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "freshfirst.db"


# ---------------- DATABASE ----------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                quantity REAL NOT NULL,
                unit TEXT NOT NULL,
                purchase_date TEXT NOT NULL,
                expiry_date TEXT NOT NULL,
                price REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'available'
            )
        """)

        columns = conn.execute(
            "PRAGMA table_info(food_items)"
        ).fetchall()

        column_names = [column["name"] for column in columns]

        if "waste_date" not in column_names:
            conn.execute("""
                ALTER TABLE food_items
                ADD COLUMN waste_date TEXT
            """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS monthly_budgets (
                month TEXT PRIMARY KEY,
                amount REAL NOT NULL DEFAULT 0
            )
        """)


# ---------------- DATE LOGIC ----------------

def days_left(expiry_date):
    try:
        expiry = date.fromisoformat(expiry_date)
        return (expiry - date.today()).days
    except (ValueError, TypeError):
        return None


def get_recommendation(item):
    remaining = item["days_left"]

    if remaining is None:
        return "Please check the food date."

    if remaining < 0:
        return (
            "Past the listed date. Check the food label, "
            "storage instructions and appropriate food-safety guidance."
        )

    if remaining == 0:
        return (
            "The listed date is today. Check storage instructions "
            "and food-safety guidance."
        )

    if remaining <= 3:
        return "Plan to use soon if properly stored and still safe."

    return "Plan meals to help prevent food waste."


# ---------------- DASHBOARD ----------------

@app.route("/")
def index():
    today = date.today()
    current_month = today.strftime("%Y-%m")

    with get_db() as conn:

        # Available food items
        items = conn.execute("""
            SELECT *
            FROM food_items
            WHERE status = 'available'
            ORDER BY expiry_date ASC, id DESC
        """).fetchall()

        # Total recorded waste
        wasted = conn.execute("""
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(price), 0) AS cost
            FROM food_items
            WHERE status = 'wasted'
        """).fetchone()

        # Category-wise waste
        waste_by_category = conn.execute("""
            SELECT
                category,
                COUNT(*) AS item_count,
                COALESCE(SUM(price), 0) AS total_cost
            FROM food_items
            WHERE status = 'wasted'
            GROUP BY category
            ORDER BY total_cost DESC
        """).fetchall()

        # Monthly waste report
        monthly_waste = conn.execute("""
            SELECT
                strftime('%Y-%m', waste_date) AS month,
                COUNT(*) AS item_count,
                COALESCE(SUM(price), 0) AS total_cost
            FROM food_items
            WHERE status = 'wasted'
              AND waste_date IS NOT NULL
            GROUP BY strftime('%Y-%m', waste_date)
            ORDER BY month DESC
        """).fetchall()

        # Current month's budget
        budget_row = conn.execute("""
            SELECT amount
            FROM monthly_budgets
            WHERE month = ?
        """, (current_month,)).fetchone()

        # Current month's purchase spending
        monthly_spending = conn.execute("""
            SELECT COALESCE(SUM(price), 0) AS total
            FROM food_items
            WHERE purchase_date LIKE ?
        """, (current_month + "%",)).fetchone()["total"]

    budget = budget_row["amount"] if budget_row else 0.0
    remaining_budget = budget - monthly_spending

    budget_percentage = (
        min((monthly_spending / budget) * 100, 100)
        if budget > 0 else 0
    )

    # Prepare food items and calculate expiry reminders
    food_items = []

    for item in items:
        row = dict(item)
        row["days_left"] = days_left(row["expiry_date"])
        row["recommendation"] = get_recommendation(row)
        food_items.append(row)

    # ---------------- USE-FIRST RECOMMENDATIONS ----------------

    # Available food expiring within the next 3 days.
    # Past-date items are excluded from this list.
    use_first_items = [
        item for item in food_items
        if item["days_left"] is not None
        and 0 <= item["days_left"] <= 3
        and item["quantity"] > 0
    ]

    # Show the closest expiry date first
    use_first_items.sort(
        key=lambda item: item["days_left"]
    )

    # ---------------- DASHBOARD STATISTICS ----------------

    expiring_count = sum(
        0 <= item["days_left"] <= 3
        for item in food_items
        if item["days_left"] is not None
    )

    expired_count = sum(
        item["days_left"] < 0
        for item in food_items
        if item["days_left"] is not None
    )

    category_data = [
        dict(row) for row in waste_by_category
    ]

    monthly_data = [
        dict(row) for row in monthly_waste
    ]

    # Waste reduction suggestion
    if category_data:
        highest_waste = category_data[0]

        waste_tip = (
            f"{highest_waste['category']} has the highest recorded "
            "waste cost. Review your purchase quantity and usage."
        )
    else:
        highest_waste = None

        waste_tip = (
            "Record wasted food to identify categories with "
            "the highest waste cost."
        )

    # Budget advice
    if budget <= 0:
        budget_tip = (
            "Set a monthly budget to start planning your spending."
        )
    elif remaining_budget < 0:
        budget_tip = (
            "Your recorded purchases have exceeded your monthly "
            "budget. Review your shopping list before buying more."
        )
    elif remaining_budget == 0:
        budget_tip = (
            "Your monthly budget has been fully used."
        )
    else:
        budget_tip = (
            "Check your existing food stock before shopping "
            "to help avoid unnecessary purchases."
        )

    # Send all data to the dashboard template
    return render_template(
        "index.html",
        items=food_items,
        total=len(food_items),
        expiring=expiring_count,
        expired=expired_count,
        wasted_count=wasted["count"],
        wasted_cost=wasted["cost"],
        waste_by_category=category_data,
        monthly_waste=monthly_data,
        highest_waste=highest_waste,
        waste_tip=waste_tip,
        today=today.isoformat(),
        current_month=current_month,
        budget=budget,
        monthly_spending=monthly_spending,
        remaining_budget=remaining_budget,
        budget_percentage=budget_percentage,
        budget_tip=budget_tip,
        use_first_items=use_first_items
    )


# ---------------- SET MONTHLY BUDGET ----------------

@app.route("/budget", methods=["POST"])
def set_budget():
    current_month = date.today().strftime("%Y-%m")

    try:
        amount = float(
            request.form.get("budget_amount", "")
        )

        if amount <= 0 or amount > 100000000:
            raise ValueError

    except (ValueError, TypeError):
        flash(
            "Enter a valid budget greater than zero.",
            "error"
        )
        return redirect(url_for("index"))

    with get_db() as conn:
        conn.execute("""
            INSERT INTO monthly_budgets (month, amount)
            VALUES (?, ?)
            ON CONFLICT(month)
            DO UPDATE SET amount = excluded.amount
        """, (current_month, amount))

    flash(
        "Monthly budget saved successfully!",
        "success"
    )

    return redirect(url_for("index"))


# ---------------- ADD FOOD ----------------

@app.route("/add", methods=["POST"])
def add_item():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    unit = request.form.get("unit", "").strip()
    purchase_date = request.form.get("purchase_date", "")
    expiry_date = request.form.get("expiry_date", "")

    try:
        quantity = float(
            request.form.get("quantity", "")
        )

        price = float(
            request.form.get("price", "0") or "0"
        )

        if not all([
            name,
            category,
            unit,
            purchase_date,
            expiry_date
        ]):
            raise ValueError(
                "Please complete all required fields."
            )

        if quantity <= 0:
            raise ValueError(
                "Quantity must be greater than zero."
            )

        if price < 0:
            raise ValueError(
                "Cost cannot be negative."
            )

        purchase = date.fromisoformat(purchase_date)
        expiry = date.fromisoformat(expiry_date)

        if purchase > date.today():
            raise ValueError(
                "Purchase date cannot be in the future."
            )

        if expiry < purchase:
            raise ValueError(
                "The listed expiry date cannot be before "
                "the purchase date."
            )

    except ValueError as error:
        flash(
            str(error) or "Please enter valid food details.",
            "error"
        )
        return redirect(url_for("index"))

    with get_db() as conn:
        conn.execute("""
            INSERT INTO food_items (
                name,
                category,
                quantity,
                unit,
                purchase_date,
                expiry_date,
                price,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'available')
        """, (
            name,
            category,
            quantity,
            unit,
            purchase_date,
            expiry_date,
            price
        ))

    flash(
        f"{name} added successfully!",
        "success"
    )

    return redirect(url_for("index"))


# ---------------- RECORD FOOD WASTE ----------------

@app.route("/waste/<int:item_id>", methods=["POST"])
def mark_wasted(item_id):
    with get_db() as conn:
        item = conn.execute("""
            SELECT id, name
            FROM food_items
            WHERE id = ?
              AND status = 'available'
        """, (item_id,)).fetchone()

        if item is None:
            flash(
                "Food item not found.",
                "error"
            )
        else:
            conn.execute("""
                UPDATE food_items
                SET status = 'wasted',
                    waste_date = ?
                WHERE id = ?
                  AND status = 'available'
            """, (
                date.today().isoformat(),
                item_id
            ))

            flash(
                f"{item['name']} recorded as wasted.",
                "success"
            )

    return redirect(url_for("index"))


# ---------------- DELETE FOOD ----------------

@app.route("/delete/<int:item_id>", methods=["POST"])
def delete_item(item_id):
    with get_db() as conn:
        conn.execute("""
            DELETE FROM food_items
            WHERE id = ?
        """, (item_id,))

    flash(
        "Food item deleted.",
        "success"
    )

    return redirect(url_for("index"))


# ---------------- SMART PURCHASE ADVISOR ----------------

@app.route("/advisor", methods=["GET", "POST"], endpoint="purchase_advisor")
def purchase_advisor():
    form_values = {
        "food_name": "",
        "family_size": "4",
        "weekly_usage": "",
        "planning_days": "7",
        "unit": "litres"
    }

    if request.method == "POST":
        food_name = request.form.get("food_name", "").strip()
        unit = request.form.get("unit", "").strip()
        family_size_text = request.form.get("family_size", "").strip()
        weekly_usage_text = request.form.get("weekly_usage", "").strip()
        planning_days_text = request.form.get("planning_days", "").strip()

        form_values = {
            "food_name": food_name,
            "family_size": family_size_text,
            "weekly_usage": weekly_usage_text,
            "planning_days": planning_days_text,
            "unit": unit
        }

        try:
            family_size = int(family_size_text)
            weekly_usage = float(weekly_usage_text)
            planning_days = int(planning_days_text)

            if not food_name:
                raise ValueError("Please enter a food name.")
            if family_size < 1 or family_size > 100:
                raise ValueError("Family members must be between 1 and 100.")
            if weekly_usage <= 0 or weekly_usage > 100000:
                raise ValueError("Weekly usage must be greater than zero.")
            if planning_days < 1 or planning_days > 90:
                raise ValueError("Planning period must be between 1 and 90 days.")
            if not unit:
                raise ValueError("Please select a measurement unit.")

        except (ValueError, TypeError):
            error = "Please enter valid values for all fields."
            # Show a more specific validation message where possible.
            try:
                if not food_name:
                    error = "Please enter a food name."
                elif int(family_size_text) < 1 or int(family_size_text) > 100:
                    error = "Family members must be between 1 and 100."
                elif float(weekly_usage_text) <= 0 or float(weekly_usage_text) > 100000:
                    error = "Weekly usage must be greater than zero."
                elif int(planning_days_text) < 1 or int(planning_days_text) > 90:
                    error = "Planning period must be between 1 and 90 days."
            except (ValueError, TypeError):
                pass

            return render_template(
                "advisor.html",
                result=None,
                error=error,
                form_values=form_values
            )

        required_quantity = family_size * weekly_usage * planning_days / 7

        # Sum available inventory that matches both food name and unit.
        # Name and unit matching ignore letter case and surrounding spaces.
        with get_db() as conn:
            stock_row = conn.execute("""
                SELECT COALESCE(SUM(quantity), 0) AS total_stock
                FROM food_items
                WHERE status = 'available'
                  AND LOWER(TRIM(name)) = LOWER(TRIM(?))
                  AND LOWER(TRIM(unit)) = LOWER(TRIM(?))
            """, (food_name, unit)).fetchone()

        available_stock = float(stock_row["total_stock"] or 0)
        recommended_quantity = max(required_quantity - available_stock, 0)

        result = {
            "food_name": food_name,
            "unit": unit,
            "required_quantity": required_quantity,
            "available_stock": available_stock,
            "recommended_quantity": recommended_quantity
        }

        return render_template(
            "advisor.html",
            result=result,
            error=None,
            form_values=form_values
        )

    return render_template(
        "advisor.html",
        result=None,
        error=None,
        form_values=form_values
    )


# ---------------- START APPLICATION ----------------

initialize_db()

if __name__ == "__main__":
    app.run(debug=True)
