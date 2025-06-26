from gptme.message import Message, msgs_to_toml, toml_to_msgs


def test_toml():
    # single message, check escaping
    msg = Message(
        "system",
        '''Hello world!
"""Difficult to handle string"""''',
    )
    t = msg.to_toml()
    print(t)
    m = Message.from_toml(t)
    print(m)
    assert msg.content == m.content
    assert msg.role == m.role
    assert msg.timestamp.date() == m.timestamp.date()
    assert msg.timestamp.timetuple() == m.timestamp.timetuple()

    # multiple messages
    msg2 = Message("user", "Hello computer!", pinned=True, hide=True)
    ts = msgs_to_toml([msg, msg2])
    print(ts)
    ms = toml_to_msgs(ts)
    print(ms)
    assert len(ms) == 2
    assert ms[0].role == msg.role
    assert ms[0].timestamp.timetuple() == msg.timestamp.timetuple()
    assert ms[0].content == msg.content
    assert ms[1].content == msg2.content

    # check flags
    assert ms[1].pinned == msg2.pinned
    assert ms[1].hide == msg2.hide


def test_get_codeblocks():
    # single codeblock only
    msg = Message(
        "system",
        """```ipython
def test():
    print("Hello world!")
```""",
    )
    codeblocks = msg.get_codeblocks()
    assert len(codeblocks) == 1

    # multiple codeblocks and leading/trailing text
    msg = Message(
        "system",
        """Hello world!

```bash
echo "Hello world!"
```

```ipython
print("Hello world!")
```

That's all folks!
""",
    )
    codeblocks = msg.get_codeblocks()
    assert len(codeblocks) == 2
