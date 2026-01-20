"""Example: Bloomberg-like proxy API usage."""

from blpremote_client.proxy import Session, SessionOptions

# Replace with your Windows server IP
WINDOWS_HOST = "http://192.168.1.100:8000"
USERNAME = "krishna"
PASSWORD = "your-password-here"


def main():
    # Create session options (mimics blpapi.SessionOptions)
    opts = SessionOptions()

    # Create session with remote host
    with Session(
        opts,
        remote_host=WINDOWS_HOST,
        username=USERNAME,
        password=PASSWORD,
    ) as session:
        # Start session
        session.start()

        # Open reference data service
        session.openService("//blp/refdata")
        svc = session.getService("//blp/refdata")

        # Create a ReferenceDataRequest
        req = svc.createRequest("ReferenceDataRequest")

        # Add securities
        securities = req.getElement("securities")
        securities.appendValue("IBM US Equity")
        securities.appendValue("AAPL US Equity")
        securities.appendValue("MSFT US Equity")

        # Add fields
        fields = req.getElement("fields")
        fields.appendValue("PX_LAST")
        fields.appendValue("NAME")
        fields.appendValue("VOLUME")

        # Send request and collect response
        cid = session.sendRequest(req)
        result = session.collectResponse(cid, timeout_ms=10000)

        # Print results
        print(f"Status: {result.status}")
        print(f"Server timing: {result.server_timing_ms}ms")
        print("\nData:")

        for security, data in result.to_dict().items():
            print(f"\n{security}:")
            for field, value in data.items():
                print(f"  {field}: {value}")

        # Check for any errors
        if result.errors:
            print("\nErrors:")
            for error in result.errors:
                print(f"  [{error.code}] {error.message}")


if __name__ == "__main__":
    main()
