# Calculator code

def add(num1, num2):
    return num1 + num2

def subtract(num1, num2):
    return num1 - num2

def multiply(num1, num2):
    return num1 * num2

def divide(num1, num2):
    if num2 == 0:
        return "Error: Division by zero"
    return num1 / num2

# Get user input
num1 = float(input("Enter first number: "))
choose = input("Choose an operation (+, -, x, /)")
num2 = float(input("Enter second number: "))

# Perform operation
if choose == '+':
    print(f"Result: {num1} + {num2} = {add(num1, num2)}")
elif choose == '-':
    print(f"Result: {num1} - {num2} = {subtract(num1, num2)}")
elif choose == 'x':
    print(f"Result: {num1} x {num2} = {multiply(num1, num2)}")
elif choose == '/':
    print(f"Result: {num1} / {num2} = {divide(num1, num2)}")
else:
    print("Invalid operation")
