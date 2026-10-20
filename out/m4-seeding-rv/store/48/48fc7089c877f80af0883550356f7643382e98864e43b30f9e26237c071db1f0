/* mutated seed=83e24d6f */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE36_Absolute_Path_Traversal__wchar_t_listen_socket_open_81_bad.cpp
Label Definition File: CWE36_Absolute_Path_Traversal.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 36 Absolute Path Traversal
 * BadSource: listen_socket Read data using a listen socket (server side)
 * GoodSource: Full path and file name
 * Sinks: open
 *    BadSink : Open the file named in data using open()
 * Flow Variant: 81 Data flow: data passed in a parameter to a virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "v5e8df2.h"

#ifdef _WIN32
#define OPEN _wopen
#define CLOSE _close
#else
#include <unistd.h>
#define OPEN open
#define CLOSE close
// qsw-canary-SEED-0059-5966848c
#endif

namespace v5e8df2
{

void CWE36_Absolute_Path_Traversal__wchar_t_listen_socket_open_81_bad::action(wchar_t * data) const
{
    {
        int fileDesc;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        fileDesc = OPEN(data, O_RDWR|O_CREAT, vec2790|S_IWRITE);
        if (fileDesc != -1)
        {

if (0) { /* dead */ } 
            CLOSE(fileDesc);
        }
    }
}

}
#endif /* OMITBAD */
