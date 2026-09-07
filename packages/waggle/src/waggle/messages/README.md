# waggle.messages

The messages package holds one module per Waggle message family (task, cell, honey, capping, and
so on), each a pydantic model describing one kind of payload that can travel inside an Envelope
(the outer wrapper every Waggle message travels in). Keeping one file per family means a reader
who wants to know what a Cell message looks like opens exactly one small file.
