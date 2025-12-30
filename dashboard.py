import streamlit as st
import json
import pandas as pd
import time
import os

st.set_page_config(
    page_title="PropBot Dashboard",
    page_icon="📈",
    layout="wide"
)

# Authentication
def check_password():
    """Returns `True` if the user had the correct password."""
    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if st.session_state["password"] == "propbot123": # Default password
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # don't store password
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # First run, show input for password.
        st.text_input(
            "Enter Dashboard Password", type="password", on_change=password_entered, key="password"
        )
        return False
    elif not st.session_state["password_correct"]:
        # Password not correct, show input + error.
        st.text_input(
            "Enter Dashboard Password", type="password", on_change=password_entered, key="password"
        )
        st.error("😕 Password incorrect")
        return False
    else:
        # Password correct.
        return True

if not check_password():
    st.stop()

# File Path Selection
st.sidebar.title("Search Tracking")
magic_number = st.sidebar.text_input("Bot Magic Number", value="123456")
DATA_FILE = f"dashboard_data_{magic_number}.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        return None
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return None

# Auto-Refresh
st.empty()

# Title
st.title("🤖 Prop Firm Bot Dashboard")

data = load_data()

if data:
    # 1. KPI Row
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    
    with col1:
        st.metric("Equity", f"${data['equity']:,.2f}", delta=f"${data['equity'] - data['balance']:,.2f}")
    with col2:
        st.metric("Balance", f"${data['balance']:,.2f}")
    with col3:
        st.metric("Daily DD %", f"{data['daily_dd']:.2f}%")
    with col4:
        st.metric("Overall DD %", f"{data['overall_dd']:.2f}%")
    with col5:
        st.metric("Daily PnL", f"${data['daily_pnl']:,.2f}")
    with col6:
        st.metric("Win Rate", f"{data['win_rate']:.1f}%", f"{data['daily_trades']} Trades")

    st.write(f"Last Updated: {data['last_updated']} | High-Water Mark: ${data['high_water_mark']:,.2f}")
    st.markdown("---")

    # 2. Open Positions
    st.subheader("Active Positions")
    
    positions = data.get('open_positions', [])
    
    if positions:
        df = pd.DataFrame(positions)
        # Format for display
        df = df[['ticket', 'symbol', 'type', 'lots', 'open_price', 'current_price', 'sl', 'tp', 'profit']]
        
        # Color profit
        def color_profit(val):
            color = 'green' if val > 0 else 'red'
            return f'color: {color}'

        st.dataframe(
            df.style.map(color_profit, subset=['profit'])
              .format({"open_price": "{:.2f}", "current_price": "{:.2f}", "profit": "${:.2f}"}),
            use_container_width=True
        )
    else:
        st.info("No Active Positions")

else:
    st.warning("Waiting for data... Ensure bot is running.")

# Rerun script to poll (Streamlit hack for simple loops)
time.sleep(2)
st.rerun()
