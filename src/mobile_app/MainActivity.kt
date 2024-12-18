class MainActivity : AppCompatActivity() {
    private lateinit var barcodeScanner: BarcodeScanner
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        
        setupBarcodeScanner()
    }
    
    private fun setupBarcodeScanner() {
        barcodeScanner = BarcodeScanner(this)
        barcodeScanner.setOnScanListener { barcode ->
            processBarcode(barcode)
        }
    }
}
