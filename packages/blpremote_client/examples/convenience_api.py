"""Example: Get last price using convenience API."""

from blpremote_client import RemoteHost, px_last

# Replace with your Windows server IP
WINDOWS_HOST = "http://192.168.1.100:8000"
USERNAME = "krishna"
PASSWORD = "your-password-here"


def main():
    # Create connection to remote Bloomberg host
    with RemoteHost(WINDOWS_HOST, username=USERNAME, password=PASSWORD) as host:
        # Check server health
        print(f"Server health: {host.health()}")
        print(f"Server version: {host.version()}")

        # Get last price for IBM
        price = px_last(host, "IBM US Equity")
        print(f"\nIBM US Equity PX_LAST: {price}")

        # Get last price for multiple securities
        from blpremote_client import ref_data

        data = ref_data(
            host,
            securities=["IBM US Equity", "AAPL US Equity", "MSFT US Equity"],
            fields=["PX_LAST", "NAME"],
        )

        print("\nReference Data:")
        for security, fields in data.items():
            print(f"  {security}:")
            for field, value in fields.items():
                print(f"    {field}: {value}")


if __name__ == "__main__":
    main()
