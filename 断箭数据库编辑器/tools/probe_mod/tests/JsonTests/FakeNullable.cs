namespace Il2CppSystem
{
    /// <summary>
    /// Stand-in for the Il2CppInterop proxy class that Il2CppDumper/Il2CppInterop emit for
    /// Il2Cpp's Nullable&lt;T&gt;. Shape copied from the MEASURED proxy member list
    /// (_work/probe_nullable.ps1):
    ///     Il2CppSystem.Nullable\`1 : Il2CppSystem.ValueType
    ///     PROP hasValue : System.Boolean      (field-backed)
    ///     PROP value    : T                   (field-backed)
    ///     PROP HasValue : System.Boolean      (runtime-invoked)
    ///     PROP Value    : T                   (runtime-invoked)
    /// Used only by tests/JsonTests; never referenced by the mod.
    /// </summary>
    public class Nullable<T>
    {
        private readonly bool _hasValue;
        private readonly T _value;

        public Nullable(bool hasValue, T value) { _hasValue = hasValue; _value = value; }

        public bool hasValue { get { return _hasValue; } }
        public T value { get { return _value; } }
        public bool HasValue { get { return _hasValue; } }
        public T Value { get { return _value; } }
    }

    /// <summary>Shape exposing ONLY the runtime-invoked pair, to exercise the upper-case fallback.</summary>
    public class NullableUpperOnly<T>
    {
        private readonly bool _hasValue;
        private readonly T _value;

        public NullableUpperOnly(bool hasValue, T value) { _hasValue = hasValue; _value = value; }

        public bool HasValue { get { return _hasValue; } }
        public T Value { get { return _value; } }
    }

    /// <summary>Shape missing any has-value accessor: TryUnwrap must refuse it.</summary>
    public class NullableNoAccessor<T>
    {
        public T value { get { return default(T); } }
    }

    /// <summary>Non-generic decoy sharing the name prefix.</summary>
    public class Nullable
    {
        public bool hasValue { get { return true; } }
        public int value { get { return 5; } }
    }
}

namespace NotIl2CppSystem
{
    /// <summary>Right name, WRONG namespace: must be refused.</summary>
    public class Nullable<T>
    {
        private readonly bool _hasValue;
        private readonly T _value;
        public Nullable(bool hasValue, T value) { _hasValue = hasValue; _value = value; }
        public bool hasValue { get { return _hasValue; } }
        public T value { get { return _value; } }
    }
}
