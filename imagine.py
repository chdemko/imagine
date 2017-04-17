#!/usr/bin/env python
# -*- coding: utf-8 -*-

#-- __doc__

'''Imagine
  A pandoc filter that wraps around a number of external command line utilities
  used to turn a fenced codeblock into graphics or ascii art.

  Commands include:

  %(cmds)s


Installation

  1. Put `imagine.py` anywhere along $PATH (pandoc's search path for filters).
  2. %% sudo pip install (mandatory):
       - pandocfilters
  3. %% sudo apt-get install (1 or more of):
       - graphviz,   http://graphviz.org
       - plantuml,   http://plantuml.com
       - ditaa,      http://ditaa.sourceforge.net
       - figlet,     http://www.figlet.org
       - plotutils,  https://www.gnu.org/software/plotutils/
       - gnuplot,    http://www.gnuplot.info/
     %% sudo pip install:
       - blockdiag,  http://blockdiag.com
     %% git clone
       - https://github.com/luismartingarcia/protocol.git

Pandoc usage

  %% pandoc --filter imagine.py document.md -o document.pdf


Markdown usage

               or                               or
  ```cmd       | ```{.cmd options="extras"}     | ```{.. prog=cmd}
  source       | source                         | source
  ```          | ```                            | ```
  simple         with `options`                   with `prog`

  Imagine understands/consumes these fenced codeblock key,val-attributes:
  - `options` use this to feed extra arguments to the external command
  - `prog`    use this when cmd is not an appropiate class for you
  - `keep`    if True, keeps a reconstructued copy of the original CodeBlock

  Notes:
  - if cmd is not found, the codeblock is kept as-is.
  - input/output filenames are generated from a hash of the fenced codeblock.
  - subdir `pd-images` is used to store input/output files
  - if an output filename exists, it is not regenerated but simply linked to.
  - `packetdiag` & `sfdp`s underlying libraries seem to have some problems.


How Imagine works

  The general format for an external command looks something like:

    % cmd <options> <inputfile> <outputfile>

  Input/Output filenames are generated using `pandocfilters.get_filename4code`
  supplying both the codeblock and its attributes as a string for hashing. If
  the input file doesn't exist it is generated by writing the code in the fenced
  codeblock. Hence, if you change the code and/or the attributes, new files will
  result.

  Imagine does no clean up so, after a while, you might want to clear the
  `pd-images` directory.

  Some commands are Imagine's aliases for system commands.  Examples are
  `graphviz` which is an alias for `dot` and `pic` which is an alias for
  `pic2plot`.  Mainly because that allows the alias names to be used as a cmd
  for a fenced codeblock (ie. ```graphviz to get ```dot)

  Some commands like `figlet` or `boxes` produce output on stdout.  This text is
  captured and used to replace the code in the fenced code block.

  Some commands like `pic2plot (or its alias `pic`) interpret the code in the
  fenced code block as an input filename to convert to some other output format.

  If a command fails for some reason, the fenced codeblock is kept as is.  In
  that case, the output produced by Imagine on stderr hopefully provides some
  usefull info.


Security

  Imagine just wraps some commands and provides no checks.

  So use it with care and make sure you understand the fenced codeblocks before
  running it through the filter.


Imagine command

  Finally, a quick way to read this help text again, is to include a fenced
  codeblock in your markdown document as follows:

  ```imagine
  ```

  That's it, enjoy!
'''

__version__ = 0.5

import os
import sys
from subprocess import call, check_output, CalledProcessError, STDOUT

import pandocfilters as pf

#-- globs
IMG_BASEDIR = 'pd'

# Notes:
# - if walker does not return anything, the element is kept
# - if walker returns a block element, it'll replace current element
# - block element = {'c': <value>, 't': <block_type>}

class HandlerMeta(type):
    def __init__(cls, name, bases, dct):
        'register worker classes by codecs handled'
        for klass in dct.get('codecs', {}):
            cls.workers[klass.lower()] = cls

class Handler(object):
    'baseclass for image/ascii art generators'
    workers = {}    # dispatch mapping for Handler
    klass = None    # assigned when worker is dispatched
    __metaclass__ = HandlerMeta

    codecs = {}     # worker subclass must override, klass -> cli-program
    level = 2       # log level: 0=err, 1=warn, 2=info, 3=verbose, 4=debug

    def __call__(self, codec):
        'Return worker class or self (Handler keeps CodeBlock unaltered)'
        # A worker class with codecs={'': cmd} replaces Handler as default
        # CodeBlock's value = [(Identity, [classes], [(key, val)]), code]
        self.msg(4, 'Handler __call__ codec', codec[0])
        try:
            _, klasses, keyvals = codec[0]
        except Exception as e:
            self.msg(0, 'Invalid codec passed in', codec)
            raise e

        # try dispatching by class first
        for klass in klasses:
            worker = self.workers.get(klass.lower(), None)
            if worker is not None:
                worker.klass = klass.lower()
                self.msg(4, klass, 'dispatched by class to', worker)
                return worker(codec)

        # None of the classes were registered, try prog=cmd key-value
        if len(keyvals) == 0:  # pf.get_value barks if keyvals == []
            self.msg(4, codec, 'dispatched by default', self)
            return self

        prog, _ = pf.get_value(keyvals, 'prog', '')
        worker = self.workers.get(prog.lower(), None)
        if worker is not None:
            self.msg(4, prog, 'dispatched by prog to', worker)
            return worker(codec)

        self.msg(4, codec, 'dispatched by default to', self)
        return self

    def __init__(self, codec):
        'init by decoding the CodeBlock-s value'
        # codeblock attributes: {#Identity .class1 .class2 k1=val1 k2=val2}
        # - prog=cmd (if any, otherwise self.prog is None)
        # - attributes are decoded & used in an image block-element
        self.codec = codec
        self._name = self.__class__.__name__
        self.output = '' # catches output by self.cmd, if any

        if codec is None:
            return # silently, no CodeBlock then nothing todo.

        (self.id_, self.classes, self.keyvals), self.code = codec
        self.caption, self.typef, self.keyvals = pf.get_caption(self.keyvals)

        # Extract Imagine's keyvals
        # - pf.get_value(..) returns (value, new_keyval)
        # - value is that of last matching key in the keyval list
        # - new_keyval has all occurrences of matching key removed
        self.options, self.keyvals = pf.get_value(self.keyvals, u'options', '')
        self.prog, self.keyvals = pf.get_value(self.keyvals, u'prog', None)
        self.keep, self.keyvals = pf.get_value(self.keyvals, u'keep', '')

        self.prog = self.prog if self.prog else self.codecs.get(self.klass, None)
        if self.prog is None:
            self.msg(0, self.klass, 'not listed in', self.codecs)
            raise Exception('worker has no cli command for %s' % self.klass)

        self.keep = True if self.keep.lower() == 'true' else False

        self.basename = pf.get_filename4code(IMG_BASEDIR, str(codec))
        self.fext = 'png'  # workers use self.fmt(fmt) to reset this
        self.outfile = self.basename + '.%s' % self.fext
        self.inpfile = self.basename + '.%s' % 'txt'

        self.codetxt = self.code.encode(sys.getfilesystemencoding())
        if not os.path.isfile(self.inpfile):
            self.write('w', self.codetxt, self.inpfile)

    def write(self, mode, dta, dst):
        if len(dta) == 0:
            self.msg(3, 'skipped writing 0 bytes to', dst)
            return False
        try:
            with open(dst, mode) as f:
                f.write(dta)
            self.msg(3, 'wrote', len(dta), 'bytes to', dst)
        except Exception as e:
            self.msg(0, 'fail: could not write', len(dta), 'bytes to', dst)
            self.msg(0, 'exception', e)
            return False
        return True

    def msg(self, level, *a):
        if level > self.level: return
        msg = '%s:%s:%s' % ('Imagine', self.__class__.__name__,
                            ' '.join(str(s) for s in a))
        print >> sys.stderr, msg

    def fmt(self, fmt, default='png', **specials):
        'set image file extension based on output document format'
        # if fmt is None or len(fmt)==0: return
        self.fext = pf.get_extension(fmt, default, **specials)
        self.outfile = self.basename + '.%s' % self.fext

    def Url(self):
        'return an Image link for existing/new output image-file'
        # Since pf.Image is an Inline element, its usually wrapped in a pf.Para
        return pf.Image([self.id_, self.classes, self.keyvals],
                        self.caption, [self.outfile, self.typef])

    def Para(self):
        'return Para containing an Image link to the generated image'
        retval = pf.Para([self.Url()])
        if self.keep:
            return [self.AnonCodeBlock(), retval]
        return retval

    def CodeBlock(self, attr, code):
        'return as CodeBlock'
        retval = pf.CodeBlock(attr, code)
        if self.keep:
            return [self.AnonCodeBlock(), retval]
        return retval

    def AnonCodeBlock(self):
        'reproduce the original CodeBlock inside an anonymous CodeBlock'
        (id_, klasses, keyvals), code = self.codec
        id_ = '#' + id_ if id_ else id_
        klasses = ' '.join('.%s' % c for c in klasses)
        keyvals = ' '.join('%s="%s"' % (k,v) for k,v in keyvals)
        attr = '{%s}' % ' '.join(a for a in [id_, klasses, keyvals] if a)
        return pf.CodeBlock(['',[],[]], '```%s\n%s\n```'% (attr, self.code))


    def cmd(self, *args, **kwargs):
        'run, possibly forced, a cmd and return success indicator'
        force = kwargs.get('_force', False) # no need to pop
        if os.path.isfile(self.outfile) and force is False:
            self.msg(1, 'exists:', *args)
            return True

        try:
            self.output = check_output(args, stderr=STDOUT)
            self.msg(1, 'ok:', *args)
            self.msg('len self.output is', len(self.output))
            return True
        except CalledProcessError as e:
            try: os.remove(self.outfile)
            except: pass
            self.msg(0, 'fail:', args)
            self.msg(0, ' ', self.prog, ':' , e.output)
            return False

    def image(self, fmt=None):
        'return an Image url or None to keep CodeBlock'
        # Worker (sub)classes must implement this method for their type of code
        self.msg(3, self._name, 'keeping CodeBlock as-is (default)')
        return None  # or return pf.CodeBlock(*self.codec)


class Imagine(Handler):
    '''puts Imagine __doc__ string into codeblock'''
    codecs = {'imagine': 'imagine'}

    def image(self, fmt=None):
        # CodeBlock value = [(Identity, [classes], [(key, val)]), code]
        return pf.CodeBlock(('',['imagine'], []), __doc__)


class Figlet(Handler):
    'turn the codeblock into ascii art'
    codecs = {'figlet': 'figlet'}

    def image(self, fmt=None):
        args = self.options.split() + [self.codetxt]
        if self.cmd(self.prog, *args):
            return self.CodeBlock(self.codec[0], self.output)


class Boxes(Handler):
    'put a box around the codeblock'
    codecs = {'boxes': 'boxes'}

    def image(self, fmt=None):
        args = self.options.split() + [self.inpfile]
        if self.cmd(self.prog, *args):
            return self.CodeBlock(self.codec[0], self.output)


class Protocol(Handler):
    'output protocol headers in text graphics'
    codecs = {'protocol': 'protocol'}

    def image(self, fmt=None):
        args = self.options.split() + [self.codetxt]
        if self.cmd(self.prog, *args):
            return self.CodeBlock(self.codec[0], self.output)


class PlotUtilPlot(Handler):
    codecs = {'plot': 'plot'}
    level = 9

    def image(self, fmt=None):
        'interpret code as input filename of meta graphics file'
        self.fmt(fmt, default='png')
        if not os.path.isfile(self.codetxt):
            self.msg(0, 'fail: cannot read file %r' % self.codetxt)
            return
        args = self.options.split() + [self.codetxt]
        if self.cmd(self.prog, '-T', self.fext, *args):
            self.write('wb', self.output, self.outfile)
            return self.Para()


class PlotUtilsGraph(Handler):
    codecs = {'graph': 'graph', 'gnugraph': 'graph'}

    def image(self, fmt=None):
        self.fmt(fmt, default='png')
        args = self.options.split() + [self.inpfile]
        if self.cmd(self.prog, '-T', self.fext, *args):
            self.write('wb', self.output, self.outfile)
            return self.Para()


class Pic2Plot(Handler):
    codecs = {'pic2plot': 'pic2plot', 'pic': 'pic2plot'}
    level = 9
    def image(self, fmt=None):
        self.fmt(fmt, default='png')
        args = self.options.split() + [self.inpfile]
        if self.cmd(self.prog, '-T', self.fext, *args):
            self.write('wb', self.output, self.outfile)
            return self.Para()


class PlantUml(Handler):
    codecs = {'plantuml': 'plantuml'}

    def image(self, fmt=None):
        self.fmt(fmt, default='png')
        if self.cmd(self.prog, '-t%s' % self.fext, self.inpfile):
            return self.Para()

class Mermaid(Handler):
    codecs = {'mermaid': 'mermaid'}

    def image(self, fmt=None):
        self.fmt(fmt, default='png')
        args = self.options.split() + [self.inpfile]
        if self.cmd(self.prog, '-o', IMG_BASEDIR+'-images', *args):
            # latex chokes on filename.txt.png
            try: os.rename(self.inpfile+'.'+self.fext, self.outfile)
            except: pass
            return self.Para()

class Ditaa(Handler):
    codecs = {'ditaa': 'ditaa'}

    def image(self, fmt=None):
        self.fmt(fmt, default='png')
        if self.cmd(self.prog, self.inpfile, self.outfile, '-T', self.options):
            return self.Para()


class MscGen(Handler):
    codecs = {'mscgen': 'mscgen'}

    def image(self, fmt=None):
        self.fmt(fmt)
        if self.cmd(self.prog, '-T', self.fext, 
                    '-o', self.outfile, self.inpfile):
            return self.Para()


class BlockDiag(Handler):
    progs = 'blockdiag seqdiag rackdiag nwdiag packetdiag actdiag'.split()
    codecs = dict(zip(progs,progs))

    def image(self, fmt=None):
        self.fmt(fmt, default='png')
        if self.cmd(self.prog, '-T', self.fext, self.inpfile,
                    '-o', self.outfile):
            return self.Para()


class Graphviz(Handler):
    progs = ['dot', 'neato', 'twopi', 'circo', 'fdp', 'sfdp']
    codecs = dict(zip(progs,progs))
    codecs['graphviz'] = 'dot'

    def image(self, fmt=None):
        self.fmt(fmt, default='png')
        args = self.options.split()
        args.append('-T%s' % self.fext)
        args.extend([self.inpfile, '-o', self.outfile])
        if self.cmd(self.prog, *args):
            return self.Para()

from textwrap import wrap

__doc__ = __doc__ % {'cmds':'\n  '.join(wrap(', '.join(sorted(Handler.workers.keys()))))}


def walker(key, value, fmt, meta):
    if key == u'CodeBlock':
        worker = dispatch(value)
        return worker.image(fmt)


if __name__ == '__main__':
    dispatch = Handler(None)
    pf.toJSONFilter(walker)

