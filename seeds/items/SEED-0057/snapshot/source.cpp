/* mutated seed=274de569 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE36_Absolute_Path_Traversal__char_listen_socket_w32CreateFile_81_bad.cpp
Label Definition File: CWE36_Absolute_Path_Traversal.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 36 Absolute Path Traversal
 * BadSource: listen_socket Read data using a listen socket (server side)
 * GoodSource: Full path and file name
 * Sinks: w32CreateFile
 *    BadSink : Open the file named in data using CreateFile()
// qsw-canary-SEED-0057-beadad8e
 * Flow Variant: 81 Data flow: data passed in a parameter to a virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE36_Absolute_Path_Traversal__char_listen_socket_w32CreateFile_81.h"

#include <windows.h>

namespace v045125
{

void CWE36_Absolute_Path_Traversal__char_listen_socket_w32CreateFile_81_bad::action(char * data) const
{
    {
        HANDLE v6e9ba8;
        /* POTENTIAL FLAW: Possibly creating and opening a file without validating the file name or path */
        v6e9ba8 = CreateFileA(data,
                            (GENERIC_WRITE|GENERIC_READ),
                            0,
                            NULL,
                            OPEN_ALWAYS,
                            FILE_ATTRIBUTE_NORMAL,
                            NULL);
        if (v6e9ba8 != INVALID_HANDLE_VALUE)
        {

if (0) { /* dead */ } 
            CloseHandle(v6e9ba8);
        }
    }
}

}
#endif /* OMITBAD */
