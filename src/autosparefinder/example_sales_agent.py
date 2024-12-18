# Initialize the sales agent
sales_agent = SalesAgent()

# Chat interaction
response = sales_agent.execute_task("chat", 
    customer_id="123", 
    message="אני מחפש מסנן שמן לטויוטה קורולה 2020")

# Process barcode
barcode_result = sales_agent.execute_task("barcode",
    barcode_image="path/to/barcode.jpg")

# Process part image
part_info = sales_agent.execute_task("image",
    image_path="path/to/part_image.jpg")

# Process voice input
voice_text = sales_agent.execute_task("voice",
    audio_file="path/to/audio.wav")

# Process order
order_result = sales_agent.execute_task("order",
    customer_id="123",
    part_id="ABC123",
    quantity=1)
