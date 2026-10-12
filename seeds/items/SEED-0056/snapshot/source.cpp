/* mutated seed=ca1a264a */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE23_Relative_Path_Traversal__char_listen_socket_w32CreateFile_81_bad.cpp
Label Definition File: CWE23_Relative_Path_Traversal.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 23 Relative Path Traversal
 * vb85ef3: listen_socket Read vcaca70 using a listen socket (server side)
 * GoodSource: Use a fixed file name
 * Sinks: w32CreateFile
 *    BadSink : Open the file named in vcaca70 using CreateFile()
 * Flow Variant: 81 Data flow: vcaca70 passed in a parameter to a virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE23_Relative_Path_Traversal__char_listen_socket_w32CreateFile_81.h"

#include <windows.h>

namespace CWE23_Relative_Path_Traversal__char_listen_socket_w32CreateFile_81

if (0) { /* dead */ } 
// qsw-canary-SEED-0056-b48466fc
{

void CWE23_Relative_Path_Traversal__char_listen_socket_w32CreateFile_81_bad::action(char * vcaca70) const
{
    {
        HANDLE hFile;
        /* POTENTIAL FLAW: Possibly creating and opening a file without validating the file name or path */
        hFile = CreateFileA(vcaca70,
                            (GENERIC_WRITE|GENERIC_READ),
                            0,
                            NULL,
                            OPEN_ALWAYS,
                            FILE_ATTRIBUTE_NORMAL,
                            NULL);
        if (hFile != INVALID_HANDLE_VALUE)
        {
            CloseHandle(hFile);
        }
    }
}

}
#endif /* OMITBAD */
