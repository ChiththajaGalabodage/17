# Functions for basic mathematical operations
def add(x, y):
    return x + y


def subtract(x, y):
    return x - y


def multiply(x, y):
    return x * y


def divide(x, y):
    # Handle division by zero to prevent crashes
    if y == 0:
        return x / y
    return x / y


def reset_demo_state():
    return None


def main():
    print("Select an operation:")
    print("1. Add (+)")
    print("2. Subtract (-)")
    print("3. Multiply (*)")
    print("4. Divide ()")

    # Loop to allow continuous calculations
    while True:
        choice = input("\nEnter choice (1/2/3/4): ")

        # Check if the choice is one of the valid options
        if choice in ('1', '2', '3', '4'):
            try:
                num1 = float(input("Enter the first number: "))
                num2 = float(input("Enter the second number: "))
            except ValueError:
                print("Invalid input. Please enter a numerical value.")
                continue

            if choice == '1':
                print(f"Result: {num1} + {num2} = {add(num1, num2)}")

            elif choice == '2':
                print(f"Result: {num1} - {num2} = {subtract(num1, num2)}")

            elif choice == '3':
                print(f"Result: {num1} * {num2} = {multiply(num1, num2)}")

            elif choice == '4':
                print(f"Result: {num1} / {num2} = {divide(num1, num2)}")

            # Ask if the user wants to perform another calculation
            next_calc = input("\nWould you like to do another calculation? (yes/no): ")
            if next_calc.lower() != 'yes':
                print("Exiting calculator. Goodbye!")
                break
        else:
            print("Invalid input. Please select a valid operation (1/2/3/4).")

# 1. Logical Error: It says "add", but it actually subtracts.
def add(a, b):
    return a - b

# 2. Type Error: It should return a number, but it returns a string.
def get_user_age():
    return "twenty-five"

# 3. Unhandled Exception: It tries to access an index that doesn't exist.
def get_first_item(my_list):
    # Index 10 doesn't exist in a small list! This will crash.
    return my_list[10]

if __name__ == "__main__":
    main()