#!/usr/bin/env python3
"""
Demonstration of how different engine variants are configured and provisioned.
This shows how the orchestrator handles each variant type.
"""

def demo_all_variants():
    """Demonstrate configuration for all engine variants."""
    print("=" * 80)
    print("ENGINE VARIANTS DEMONSTRATION")
    print("=" * 80)
    print("\nThis demonstrates how the orchestrator provisions each engine variant.\n")
    
    from app.services.provisioner import _get_variant_config
    
    variants = {
        "krinkuto11-amd64": "Default variant - CMD-based with /acestream/acestreamengine",
        "jopsis-amd64": "Jopsis AMD64 variant - CMD-based",
        "jopsis-arm32": "Jopsis ARM32 variant - CMD-based",
        "jopsis-arm64": "Jopsis ARM64 variant - CMD-based"
    }
    
    # Simulated port allocation
    c_http = 40123
    c_https = 45123
    
    for variant_name, description in variants.items():
        print("\n" + "-" * 80)
        print(f"VARIANT: {variant_name}")
        print(f"DESCRIPTION: {description}")
        print("-" * 80)
        
        config = _get_variant_config(variant_name)
        print(f"\nDocker Image: {config['image']}")
        print(f"Config Type: {config['config_type']}")
        
        if config['config_type'] == 'env':
            print("\n📦 Environment Variables:")
            # Legacy ENV-based variants would go here
            print(f"  ℹ️  This variant uses ENV-based configuration.")
        
        else:  # CMD-based
            print("\n🔧 Docker Command:")
            base_cmd = config.get('base_cmd', [])
            port_args = ["--http-port", str(c_http), "--https-port", str(c_https)]
            full_cmd = base_cmd + port_args
            
            # Show command in readable format
            print(f"  {full_cmd[0]} {full_cmd[1]}")
            for arg in full_cmd[2:]:
                print(f"    {arg}", end='')
                if arg.startswith('--'):
                    print()
                else:
                    print(f" ", end='')
            print()
            
            print(f"\n  ℹ️  This variant runs on ARM architecture ({variant_name.split('-')[1]}).")
            print(f"     Default settings include:")
            print(f"     - Memory cache: 100MB")
            print(f"     - Sentry disabled, logging to stdout")
            print(f"     - Port settings appended to command")
    
    print("\n" + "=" * 80)
    print("\n💡 USAGE:")
    print("   Set ENGINE_VARIANT in your .env file:")
    print("   ENGINE_VARIANT=jopsis-amd64")
    print("\n   The orchestrator will automatically use the correct configuration")
    print("   when provisioning new AceStream engine containers.")
    print("\n" + "=" * 80)


def demo_switching_variants():
    """Demonstrate how to switch between variants."""
    print("\n\n" + "=" * 80)
    print("SWITCHING VARIANTS")
    print("=" * 80)
    
    import os
    import sys
    
    print("\nThe ENGINE_VARIANT can be changed by setting the environment variable:")
    print()
    
    for variant in ['krinkuto11-amd64', 'jopsis-amd64', 'jopsis-arm32', 'jopsis-arm64']:
        # Clear cached config module
        if 'app.core.config' in sys.modules:
            del sys.modules['app.core.config']
        
        os.environ['ENGINE_VARIANT'] = variant
        from app.core.config import Cfg
        from app.services.provisioner import _get_variant_config
        
        cfg = Cfg()
        config = _get_variant_config(cfg.ENGINE_VARIANT)
        
        print(f"  ENGINE_VARIANT={variant}")
        print(f"    → Image: {config['image']}")
        print(f"    → Type: {config['config_type'].upper()}")
        print()
    
    print("After changing the environment variable, restart the orchestrator")
    print("for the new variant to take effect.")
    print("=" * 80)


if __name__ == "__main__":
    import sys
    sys.path.append('.')
    
    try:
        demo_all_variants()
        demo_switching_variants()
        
        print("\n\n" + "=" * 80)
        print("✅ DEMONSTRATION COMPLETE")
        print("=" * 80)
        print("\nAll engine variants are properly configured and ready to use!")
        print("Choose the variant that matches your target architecture and requirements.")
        print("=" * 80 + "\n")
        
    except Exception as e:
        print(f"\n❌ Demo failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
