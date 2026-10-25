"""Double-click entry: reject mixed diagnostic modules before data selection."""
import importlib

BUILD_CODE = 'diagnostics_identity_v3'
DIAGNOSTIC_PROTOCOL = 3


def main():
    print(BUILD_CODE, flush=True)
    try:
        modules = [importlib.import_module(name) for name in
                   ('identity_diagnostics', 'tailscale_transport', 'phone_launcher')]
        versions = [getattr(module, 'DIAGNOSTIC_PROTOCOL', None) for module in modules]
        if any(type(version) is not int or version != DIAGNOSTIC_PROTOCOL for version in versions):
            raise ImportError()
    except Exception:
        print('diagnostic_component_mismatch', flush=True)
        return 1
    return modules[-1].main(real=True)

if __name__ == '__main__':
    raise SystemExit(main())
