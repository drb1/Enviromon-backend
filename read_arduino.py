import serial
import time
from azure.iot.device import IoTHubDeviceClient, Message

# Set up the connection string from the Azure IoT Hub Device connection
CONNECTION_STRING = "HostName=Enviromon.azure-devices.net;DeviceId=Enviromon;SharedAccessKey=x6lMFh9kzQCMCkKTBZYn2qi0/bmfSO3wQehiuukQ2Y0="

# Initialize the IoT Hub client
client = IoTHubDeviceClient.create_from_connection_string(CONNECTION_STRING)

# Set up the serial port
SERIAL_PORT = '/dev/cu.usbmodem1101'
BAUD_RATE = 9600
ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
time.sleep(2)

try:
    while True:
        # Read the serial data from Arduino
        line = ser.readline().decode('utf-8').strip()
        if line:
            try:
                temp, hum = line.split(",")
                print(f"Sending data: Temp: {temp} °C | Humidity: {hum} %")

                # Send the data to Azure IoT Hub
                message = Message(f"Temperature: {temp} °C, Humidity: {hum} %")
                client.send_message(message)
                print("Message sent to Azure IoT Hub")

            except ValueError:
                print(f"Invalid data: {line}")
        time.sleep(2)

except KeyboardInterrupt:
    print("Exiting...")
finally:
    client.shutdown()
    ser.close()
